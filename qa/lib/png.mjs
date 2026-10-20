/**
 * Real pixel size of a PNG from its header bytes.
 *
 * WHY: the runtime computes UVs from the atlas page's declared `size:` and
 * never looks at the image, so a page whose PNG was resized or re-exported
 * renders with every region shifted and nothing errors. Only reading the
 * file itself catches that (gate L7). The IHDR chunk is always first, so 24
 * bytes are enough; no image library is needed.
 */
import fs from "node:fs";

const SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

/** {width, height} of the PNG at `file`; throws with a reason when it is not one. */
export function pngSize(file) {
  const buf = Buffer.alloc(24);
  const fd = fs.openSync(file, "r");
  try {
    if (fs.readSync(fd, buf, 0, 24, 0) < 24) throw new Error("file shorter than a PNG header");
  } finally {
    fs.closeSync(fd);
  }
  if (!SIGNATURE.every((b, i) => buf[i] === b)) throw new Error("not a PNG (bad signature)");
  if (buf.toString("latin1", 12, 16) !== "IHDR") throw new Error("PNG does not start with IHDR");
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}
