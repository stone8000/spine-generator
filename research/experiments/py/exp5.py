import math
def spine_step_resp(strength, mass, damping, fps=60, T=3.0):
    t=1/fps; d=damping**(60*t); m=t/mass
    x=-1.0; v=0.0; mn=x; mx=x; out=[]  # offset starts displaced by -1 (bone jerked away), returns to 0
    for i in range(int(T*fps)):
        v += (-x*strength)*m; x += v*t; v *= d
        out.append(x)
    return max(out)  # overshoot past 0
for s,ms,dmp in [(100,1,0.85),(100,1,0.95),(200,1,0.85),(50,1,0.9)]:
    w=math.sqrt(s/ms); c=-60*math.log(dmp); z=c/(2*w)
    pred=math.exp(-z*math.pi/math.sqrt(1-z*z)) if z<1 else 0
    print('strength %d mass %d damping %.2f -> f %.2f Hz, zeta %.2f, predicted overshoot %.2f, simulated %.2f'%(s,ms,dmp,w/2/math.pi,z,pred,spine_step_resp(s,ms,dmp)))
