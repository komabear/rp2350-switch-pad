"""Stream neutral frames to the PIO link for ~1.2 s and report write success.
  py tools/link_test.py [label]
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'host'))
import bridge, bridge_hid as bh
tag=sys.argv[1] if len(sys.argv)>1 else 'link'
d=bh.open_link()
f=bridge.build_frame(bridge.NEUTRAL_GAMEPAD)
ok=bad=0
for i in range(150):
    if d.send_feature_report(bh.to_report(f,i+1))>=0: ok+=1
    else: bad+=1
    time.sleep(0.008)
d.close()
print(tag,'ok',ok,'bad',bad)
