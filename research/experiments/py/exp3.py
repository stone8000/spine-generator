import numpy as np
dt=1/240
t=np.arange(-1,2,dt)
# plain move 0->1 over 0.433..0.533 (0.1 s strike), hold
u=np.clip((t-0.433)/0.1,0,1); x=u*u*(3-2*u)
def caf(x,sigma,A):
    k=np.arange(-4*sigma,4*sigma+dt,dt)
    g=np.exp(-k**2/(2*sigma**2)); g/=g.sum()
    xs=np.convolve(x,g,mode='same')
    acc=np.gradient(np.gradient(xs,dt),dt)
    return x - A*acc
for sigma in (0.05,0.08,0.12):
    for A in (0.002,0.005,0.01):
        y=caf(x,sigma,A)
        m=(t>-0.5)&(t<1.5)
        pre=-y[m].min(); over=y[m].max()-1
        print('sigma %.2f A %.3f  anticipation %.2f  overshoot %.2f  (of amplitude)'%(sigma,A,pre,over))
