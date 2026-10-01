"""Road correlation helpers migrated from the Wiki registration experiment."""
import cv2
import numpy as np

SCALE=.25
PAD=180
ROI=(25,96,243,307)

def masks(image):
    """从历史 Wiki 截图裁出紧凑地图，屏蔽控件、亮图标和圆环后返回道路及有效掩码。"""
    x,y,r,b=ROI
    im=image[y:b,x:r]
    hsv=cv2.cvtColor(im,cv2.COLOR_BGR2HSV)
    road=cv2.inRange(hsv,(85,65,115),(115,255,255))
    road=cv2.morphologyEx(road,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    valid=np.full(road.shape,255,np.uint8)
    valid[:3]=valid[-3:]=0;valid[:,:3]=valid[:,-3:]=0
    valid[:29,:42]=0;valid[:29,-34:]=0;valid[-31:,-76:]=0
    bright=(hsv[:,:,2]>225).astype(np.uint8)*255
    bright[72:130,78:138]=0
    valid[cv2.dilate(bright,np.ones((15,15),np.uint8))>0]=0
    cv2.circle(valid,(109,102),15,0,-1)
    road[valid==0]=0
    return im,road,valid

def template_matrix(projection,a,b,shape):
    """将候选尺度与透视投影组合到粗搜索分辨率，并平移模板角点至正坐标。"""
    h,w=shape
    T=np.array([[a,0,243-a*w/2],[0,a,231-a*h/2],[0,0,1.0]])
    matrix=np.diag([b*SCALE,b*SCALE,1.])@projection@T
    corners=cv2.perspectiveTransform(np.array([[[0,0],[w,0],[w,h],[0,h]]],float),matrix)[0]
    lo=np.floor(corners.min(axis=0));size=np.ceil(corners.max(axis=0)-lo).astype(int)
    matrix=np.array([[1,0,-lo[0]],[0,1,-lo[1]],[0,0,1.]])@matrix
    return matrix,tuple(size)

def match(road,valid,matrix,size,target):
    """计算粗搜索道路 IoU，屏蔽最佳峰附近后保留独立候选，供歧义门槛检查。"""
    r=cv2.warpPerspective(road,matrix,size,flags=cv2.INTER_AREA).astype(np.float32)/255
    v=cv2.warpPerspective(valid,matrix,size,flags=cv2.INTER_NEAREST).astype(np.float32)/255
    r*=v
    if r.sum()<30:return None
    intersection=cv2.matchTemplate(target,r,cv2.TM_CCORR)
    total=cv2.matchTemplate(target,v,cv2.TM_CCORR)
    iou=intersection/np.maximum(total+r.sum()-intersection,1)
    _,score,_,loc=cv2.minMaxLoc(iou)
    # Keep the strongest spatially distinct alternative, not a neighbouring subpixel peak.
    other=iou.copy();cv2.circle(other,loc,round(130*SCALE),0,-1)
    _,second,_,second_loc=cv2.minMaxLoc(other)
    p=cv2.perspectiveTransform(np.array([[[109.,102.]]]),matrix)[0,0]
    return {'score':score,'second':second,'position':((np.array(loc)+p-PAD)/SCALE).tolist(),
            'translation':list(loc),'matrix':matrix.tolist(),'size':[int(v) for v in size]}

