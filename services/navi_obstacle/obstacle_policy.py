"""Scene-specific RGB observations; no depth, free-space or detour claims."""
import numpy as np
from scipy import ndimage

POLICY_VERSION = 2
MIN_CONFIDENCE = .65
# Match this model's label names: Mapillary person=19; ADE20K chair=19.
INDOOR_OBJECTS = {
    'person': '行人', 'chair': '椅子', 'armchair': '扶手椅',
    'swivel chair': '办公椅', 'seat': '座椅', 'sofa': '沙发',
    'stool': '凳子', 'ottoman': '脚凳', 'bench': '长椅', 'table': '桌子',
    'desk': '办公桌', 'coffee table': '茶几', 'box': '箱子',
    'cabinet': '柜子', 'shelf': '置物架', 'bookcase': '书柜',
    'wardrobe': '衣柜', 'chest of drawers': '抽屉柜', 'counter': '柜台',
    'ashcan': '垃圾桶', 'bag': '包', 'basket': '篮子', 'plant': '盆栽',
    'column': '立柱', 'door': '门', 'screen door': '门',
    'stairs': '楼梯', 'stairway': '楼梯', 'step': '台阶',
}
OUTDOOR_OBJECTS = {
    'Person': '行人', 'Bicyclist': '骑车人', 'Motorcyclist': '骑车人',
    'Other Rider': '骑车人', 'Bench': '长椅', 'Bike Rack': '自行车架',
    'Fire Hydrant': '消防栓', 'Junction Box': '设备箱', 'Mailbox': '邮箱',
    'Phone Booth': '电话亭', 'Pothole': '坑洼', 'Street Light': '路灯',
    'Pole': '杆体', 'Utility Pole': '电线杆', 'Trash Can': '垃圾桶',
    'Bicycle': '自行车', 'Bus': '公交车', 'Car': '汽车', 'Caravan': '房车',
    'Motorcycle': '摩托车', 'On Rails': '轨道车辆', 'Other Vehicle': '车辆',
    'Trailer': '拖车', 'Truck': '卡车', 'Wheeled Slow': '轮式物体',
}


def detection_message(detection):
    prefix = detection['position']+'识别到'+detection['label']
    if detection['label'] == '门':
        return prefix+'，请观察开闭状态。'
    if detection['label'] in ('楼梯', '台阶'):
        return prefix+'，请停下确认。'
    return prefix+('，请停下观察。' if detection['level'] == 'caution' else '，请留意周围。')


def observe(labels, confidence, label_names, profile='indoor'):
    labels = np.asarray(labels)
    confidence = np.asarray(confidence)
    if labels.ndim != 2 or confidence.shape != labels.shape or not labels.size:
        raise ValueError('Mask dimensions must agree and be nonempty')
    if profile not in ('indoor', 'outdoor'):
        raise ValueError('Unknown scene profile')
    names = {int(k): str(v).strip() for k, v in label_names.items()}
    objects = INDOOR_OBJECTS if profile == 'indoor' else OUTDOOR_OBJECTS
    h, w = labels.shape
    roi = np.zeros((h, w), dtype=bool)
    roi[int(h*.30):int(h*.98), int(w*.12):int(w*.88)] = True
    detections = []
    for class_id, model_label in names.items():
        name = objects.get(model_label)
        if name is None:
            continue
        mask = (labels == class_id) & np.isfinite(confidence) & (confidence >= MIN_CONFIDENCE) & roi
        components, _ = ndimage.label(mask)
        for index, slices in enumerate(ndimage.find_objects(components), start=1):
            if slices is None:
                continue
            y, x = np.where(components[slices] == index)
            minimum_area = .004 if name == '行人' else .008
            if len(x) < max(24, h*w*minimum_area):
                continue
            y, x = y+slices[0].start, x+slices[1].start
            box = [float(x.min()/w), float(y.min()/h),
                   float((x.max()+1)/w), float((y.max()+1)/h)]
            center = float(x.mean()/w)
            position = '左前方' if center < .38 else '右前方' if center > .62 else '前方'
            central = ((x >= w*.35) & (x < w*.65)).sum()/max(1, h*w*.30)
            caution = central > .045 and box[3] > .68
            detections.append(dict(
                class_id=class_id, label=name, model_label=model_label,
                position=position, box=box,
                confidence=round(float(confidence[y, x].mean()), 3),
                occupancy=round(len(x)/(h*w), 4),
                level='caution' if caution else 'notice', distance_m=None))
    detections.sort(key=lambda d: (d['level'] == 'caution', d['occupancy']), reverse=True)
    detections = detections[:4]
    message = detection_message(detections[0]) if detections else '本帧未识别到可提示障碍，不代表通行安全。'
    return dict(detections=detections, raw_predictions=_raw_predictions(labels, confidence, roi, names, objects, detections),
                message=message, level=detections[0]['level'] if detections else 'unknown',
                profile=profile, policy_version=POLICY_VERSION,
                distance_available=False, automatic_detour=False)


def _raw_predictions(labels, confidence, roi, names, objects, detections):
    raw = []
    selected = {d['class_id'] for d in detections}
    for class_id, count in zip(*np.unique(labels[roi], return_counts=True)):
        pixels = (labels == class_id) & roi & np.isfinite(confidence)
        if not pixels.any():
            continue
        name = names.get(int(class_id), 'unknown')
        raw.append(dict(class_id=int(class_id), model_label=name,
                        confidence=round(float(confidence[pixels].mean()), 3),
                        occupancy=round(int(count)/labels.size, 4),
                        disposition=('candidate' if int(class_id) in selected else
                                     'filtered_scene' if name not in objects else
                                     'below_confidence_or_area')))
    raw.sort(key=lambda d: d['occupancy'], reverse=True)
    return raw[:12]
