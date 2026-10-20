import json, sys, numpy as np, cv2, triangle as tr
from PIL import Image
base='/home/user/chenyuping1998/wp/apps/HardTime/static/assets/spines/htCast/'
who=sys.argv[1]
d=json.load(open(base+who+'.json'))
page=np.array(Image.open(base+who+'.png').convert('RGBA'))
lines=open(base+who+'.atlas').read().split('\n')
rects={}
for i,l in enumerate(lines):
    if l.startswith('bounds:'):
        x,y,w,h=map(int,l[7:].split(',')); rects[lines[i-1]]=(x,y,w,h)
def tri_area(P,T):
    a=P[T[:,0]];b=P[T[:,1]];c=P[T[:,2]]
    return np.abs(0.5*((b[:,0]-a[:,0])*(c[:,1]-a[:,1])-(b[:,1]-a[:,1])*(c[:,0]-a[:,0])))
def coverage(P,T,alpha):
    # rasterize triangles, measure transparent fraction and opaque pixels missed
    m=np.zeros(alpha.shape,np.uint8)
    for t in T:
        cv2.fillConvexPoly(m, np.round(P[t]*16).astype(np.int32), 1, shift=4)
    op=alpha>8
    return (m.astype(bool)&~op).sum()/max(1,m.sum()), (op&~m.astype(bool)).sum()/max(1,op.sum())
def cdt_mesh(alpha, eps=1.5, maxarea=None):
    op=(alpha>8).astype(np.uint8)
    op=cv2.dilate(op,np.ones((3,3),np.uint8))
    cs,_=cv2.findContours(op,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_NONE)
    V=[];S=[]
    for c in cs:
        if cv2.contourArea(c)<30: continue
        a=cv2.approxPolyDP(c,eps,True)[:,0,:].astype(float)
        o=len(V); n=len(a); V+=a.tolist(); S+=[[o+i,o+(i+1)%n] for i in range(n)]
    opts='pq28'+(f'a{maxarea}' if maxarea else '')
    # holes: none handled (CCOMP inner contours become segments; triangle fills them unless holes given)
    t=tr.triangulate({'vertices':np.array(V),'segments':np.array(S)},opts)
    return t['vertices'], t['triangles']
print(f'{who}: attachment | ours: verts tris transparent%  | CDT eps1.5 q28 a(22^2/2): verts tris transparent% missed%')
tot=[0,0,0,0]
for slot,atts in d['skins'][0]['attachments'].items():
    for n,a in atts.items():
        if a.get('type')!='mesh' or n not in rects: continue
        x,y,w,h=rects[n]; alpha=page[y:y+h,x:x+w,3]
        uv=np.array(a['uvs']).reshape(-1,2); P=uv*np.array([w,h]); T=np.array(a['triangles']).reshape(-1,3)
        tw,miss=coverage(P,T,alpha)
        V2,T2=cdt_mesh(alpha,float(sys.argv[2]),int(sys.argv[3]))
        tw2,miss2=coverage(V2,T2,alpha)
        tot[0]+=len(P);tot[1]+=len(V2)
        print(f'  {n:14s} | {len(P):4d} {len(T):4d} {tw*100:5.1f}% | {len(V2):4d} {len(T2):4d} {tw2*100:5.1f}% {miss2*100:4.1f}%')
print('total verts ours',tot[0],'cdt',tot[1])
