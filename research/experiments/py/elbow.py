import numpy as np, triangle as tr, igl, math, time
# Arm outline: shoulder at x=0, elbow at x=100, wrist x=190, hand blob to 230. width 44 tapering to 36.
def outline():
    top=[]; bot=[]
    for x in np.linspace(0,190,39):
        hw = 22 - 4*(x/190)
        top.append((x, hw)); bot.append((x,-hw))
    hand=[(190+20+20*math.cos(a), 20*math.sin(a)) for a in np.linspace(-math.pi/2*1.0, math.pi/2*1.0, 13)]
    # polygon: bottom left->right, hand, top right->left
    pts = bot + [(p[0],p[1]) for p in hand] + top[::-1]
    return np.array(pts)
P = outline()
n=len(P); segs=np.array([[i,(i+1)%n] for i in range(n)])
def mesh(opts):
    # bone skeleton embedded as constrained interior segments (needed by igl.boundary_conditions)
    bx=np.linspace(1,229,58); B=np.stack([bx,np.zeros_like(bx)],1)
    VV=np.vstack([P,B]); m=len(P)
    bs=np.array([[m+i,m+i+1] for i in range(len(B)-1)])
    t=tr.triangulate({'vertices':VV,'segments':np.vstack([segs,bs])}, opts)
    return t['vertices'], t['triangles']
J = np.array([[1.,0.],[100.,0.],[229.,0.]])
BE = np.array([[0,1],[1,2]])
def smooth(e0,e1,v):
    t=np.clip((v-e0)/(e1-e0),0,1); return t*t*(3-2*t)
def ramp(V, fade):
    s = V[:,0]
    wf = smooth(100-fade,100+fade,s)
    return np.stack([1-wf, wf],1)
def bbw(V,F,free=2):
    b=[];bc=[]
    for i,v in enumerate(V):
        if abs(v[1])<1e-6 and 1<=v[0]<=229 and abs(v[0]-100)>free:
            b.append(i); bc.append([1.,0.] if v[0]<100 else [0.,1.])
    b=np.array(b,dtype=np.int64); bc=np.array(bc)
    W = igl.bbw(V, F, b, bc, partition_unity=True, max_iter=200)
    W = np.clip(W,0,1); W/=W.sum(1,keepdims=True)
    return W
def rot(th):
    c,s=math.cos(th),math.sin(th); return np.array([[c,-s],[s,c]])
def pose(V, W, th, mid=None):
    # bone0 identity, bone1 rotates th about elbow; optional mid bone rotating th/2
    R = rot(th); E=np.array([100.,0.])
    T0 = V.copy()
    T1 = (V-E)@R.T + E
    out = W[:,0:1]*T0 + W[:,1:2]*T1
    if W.shape[1]==3:
        Rm=rot(th/2); T2=(V-E)@Rm.T+E
        out += W[:,2:3]*T2
    return out
def area(V,F):
    a=V[F[:,0]];b=V[F[:,1]];c=V[F[:,2]]
    return 0.5*((b[:,0]-a[:,0])*(c[:,1]-a[:,1])-(b[:,1]-a[:,1])*(c[:,0]-a[:,0]))
def metrics(V,F,W,th):
    A0=area(V,F); A1=area(pose(V,W,th),F)
    flips=int(np.sum(np.sign(A0)!=np.sign(A1)))
    ratio=A1/A0
    # region near elbow (|x-100|<50)
    c=V[F].mean(1); near=np.abs(c[:,0]-100)<50
    Vp=pose(V,W,th); k=np.argmin(np.hypot(V[:,0]-100,V[:,1]+20.4)); r=np.hypot(*(Vp[k]-np.array([100.,0.])))/np.hypot(*(V[k]-np.array([100.,0.])))
    return flips, ratio[near].min(), A1[near].sum()/A0[near].sum(), r
def pw_mid(W):
    s=W[:,1]; a=np.clip(2*s,0,1); b=np.clip(2*s-1,0,1)
    return np.stack([1-a, b, a-b],1)
def tri_mid(W):
    # convert 2-bone ramp weight into 3 bones (upper, fore, mid) using Bernstein split
    s=W[:,1]; return np.stack([(1-s)**2, s**2, 2*s*(1-s)],1)
for opts in ['pq30a60']:
    V,F = mesh(opts)
    t=time.time(); Wb=bbw(V,F); tb=time.time()-t
    print(f'mesh {opts}: {len(V)} verts {len(F)} tris, bbw {tb*1000:.0f} ms')
    for name,W in [('ramp fade40 (ours)',ramp(V,40)),('ramp fade20 (StretchyStudio-like 40px)',ramp(V,20)),('BBW (bone constraint to 2px of joint)',Wb),('BBW free zone 22px (=limb radius)',bbw(V,F,22)),('BBW free zone 44px',bbw(V,F,44)),('ramp40 + half-angle mid bone',tri_mid(ramp(V,40))),('BBW + half-angle mid bone',tri_mid(Wb)),('ramp40 + mid bone, piecewise (no 3-way mix)',pw_mid(ramp(V,40))),('ramp60 + mid bone, piecewise',pw_mid(ramp(V,60)))]:
        row=[]
        for deg in (60,90,120,150):
            f,mn,ar,r = metrics(V,F,W,math.radians(deg))
            row.append(f'{deg}: flips={f:2d} area={ar:.2f} outerR={r:.2f}')
        print(f'  {name:40s} | '+' | '.join(row))
