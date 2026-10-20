import sys, numpy as np
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
from spinegen.curves import evaluate
W,S,H,E=0.333,0.433,0.667,1.0
k=1.35
P=[(0,0,'e'),(W,-12*k,'e'),(S,42*k,'snap'),(H,35*k,'e'),(E,0,'e')]
def fn(t):
    for i in range(len(P)-1):
        t0,v0,_=P[i]; t1,v1,ease=P[i+1]
        if t0<=t<=t1:
            u=(t-t0)/(t1-t0); e=1-(1-u)**3 if ease=='snap' else u*u*(3-2*u); return v0+(v1-v0)*e
    return P[-1][1]
keys=[]
for i,(t0,v0,_) in enumerate(P):
    k_={'time':t0,'value':v0}
    if i<len(P)-1:
        t1,v1,ease=P[i+1]; h=t1-t0
        if ease=='snap':  # bernstein 0,1,1,1
            c=[t0+h/3, v1, t1-h/3, v1]
        else:             # smoothstep bernstein 0,0,1,1
            c=[t0+h/3, v0, t1-h/3, v1]
        k_['curve']=c
    keys.append(k_)
tt=np.linspace(0,1,2401)
err=max(abs(evaluate(keys,'value',t)-fn(t)) for t in tt)
print('5 analytic keys, runtime-polyline max error vs intended: %.3f deg'%err)
