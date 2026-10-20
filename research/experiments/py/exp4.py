import math, numpy as np
def sod(x, T, f, z, r):
    k1=z/(math.pi*f); k2=1/((2*math.pi*f)**2); k3=r*z/(2*math.pi*f)
    y=x[0]; yd=0; xp=x[0]; out=[]
    for xi in x:
        xd=(xi-xp)/T; xp=xi
        k2s=max(k2, T*T/2+T*k1/2, T*k1)
        y=y+T*yd
        yd=yd+T*(xi+k3*xd-y-k1*yd)/k2s
        out.append(y)
    return np.array(out)
T=1/240; t=np.arange(0,2,T)
x=(t>=0.2).astype(float)  # step at 0.2s
for f,z,r in [(2,0.5,0),(2,0.5,2),(2,1,-1),(2,1,-2),(3,0.7,-1.5),(2,0.35,0)]:
    y=sod(x,T,f,z,r)
    ant=-min(0,y.min()); ov=max(0,y.max()-1)
    t90=t[np.argmax(y>=0.9)]-0.2
    print('f=%.1f zeta=%.2f r=%+.1f  anticipation %.2f  overshoot %.2f  t90 %.3fs'%(f,z,r,ant,ov,t90))
