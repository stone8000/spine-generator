import sys, math, numpy as np
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
from spinegen.curves import fit, max_error
FPS=30
W,S,H,E=0.333,0.433,0.667,1.0
def pose(poses):
    def fn(t):
        for i in range(len(poses)-1):
            t0,v0,_=poses[i]; t1,v1,ease=poses[i+1]
            if t0<=t<=t1:
                u=(t-t0)/(t1-t0)
                e=1-(1-u)**3 if ease=='snap' else u*u*(3-2*u)
                return v0+(v1-v0)*e
        return poses[-1][1]
    return fn
# warden fore_r in react: keys(-12k, 42k, 35k), k=1.35
k=1.35
fn=pose([(0,0,'e'),(W,-12*k,'e'),(S,42*k,'snap'),(H,35*k,'e'),(E,0,'e')])
# 1) the 30fps linear samples as stored now
ts30=[i/FPS for i in range(31)]
vs30=[fn(t) for t in ts30]
# what the runtime shows from linear 30fps keys vs the intended function at 240 Hz
tt=np.linspace(0,1,241)
lin=np.interp(tt,ts30,vs30)
true=np.array([fn(t) for t in tt])
print('30fps-linear keys:',len(ts30),' max deviation from intended curve: %.2f deg'%np.abs(lin-true).max())
# 2) fit intended curve directly
for tol in (0.5,0.15):
    keys,rep=fit(list(tt),{'value':list(true)},tol)
    print('fit intended tol',tol,'keys',len(keys),'err',rep)
# 3) second-order follow-through: child driven by parent with spring (Spine physics semantics)
def spine_spring(x_target, dt, strength=100, mass=1, damping=0.85, inertia=0.5, step=1/60):
    # offset follows -inertia*delta of target; spring pulls offset to 0
    off=0; vel=0; prev=x_target[0]; out=[]
    m=1/mass*step; d=damping**(60*step)
    sub=int(round(dt/step))
    for x in x_target:
        off += (prev-x)*inertia; prev=x
        for _ in range(sub):
            vel += (-off*strength)*m; off += vel*step; vel*=d
        out.append(x+off)
    return np.array(out)
dt=1/240
tt2=np.arange(0,2.0+1e-9,dt)
par=np.array([fn(t) if t<=1 else 0 for t in tt2])
# can't substep 1/60 at dt 1/240 exactly; use step=1/240 for illustration
child=spine_spring(par,dt,step=1/240)
omega=math.sqrt(100/1); c=-60*math.log(0.85)
print('Spine physics default strength100 mass1 damping0.85 -> f=%.2f Hz zeta=%.2f'%(omega/2/math.pi, c/(2*omega)))
i_peak=np.argmax(par); j_peak=np.argmax(child)
print('parent peak %.1f at %.3fs, child-with-physics peak %.1f at %.3fs'%(par[i_peak],tt2[i_peak],child[j_peak],tt2[j_peak]))
