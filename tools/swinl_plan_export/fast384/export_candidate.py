"""Export a separate lower-resolution Swin-L candidate from the local checkpoint."""
import argparse
import json
import time
import inspect
import textwrap
from pathlib import Path
import numpy as np
import torch
from torch import nn
import torch.nn.functional as F
from transformers import Mask2FormerConfig, Mask2FormerForUniversalSegmentation
import transformers.models.mask2former.modeling_mask2former as mask2former_impl


class InferenceOnlyLoss(nn.Module):
    def __init__(self, *args, **kwargs):
        super().__init__()

    def forward(self, *args, **kwargs):
        raise RuntimeError('training loss is unavailable in the export-only candidate')


# Transformers constructs the criterion even in eval mode. Avoid installing
# SciPy solely for that unused training path.
mask2former_impl.Mask2FormerLoss = InferenceOnlyLoss

# Preserve the input-dependent attention mask while avoiding ONNX ScatterND.
# The indexed assignment clears precisely the rows whose entries are all True.
original_forward = mask2former_impl.Mask2FormerMaskedAttentionDecoder.forward
forward_source = textwrap.dedent(inspect.getsource(original_forward))
old = 'attention_mask[torch.where(attention_mask.sum(-1) == attention_mask.shape[-1])] = False'
new = 'attention_mask = attention_mask & (attention_mask.sum(-1) != attention_mask.shape[-1]).unsqueeze(-1)'
assert forward_source.count(old) == 1
namespace = dict(vars(mask2former_impl))
exec(compile(forward_source.replace(old, new), '<export_attention_mask>', 'exec'), namespace)
mask2former_impl.Mask2FormerMaskedAttentionDecoder.forward = namespace['forward']

parser = argparse.ArgumentParser()
parser.add_argument('--width', type=int, default=384)
parser.add_argument('--height', type=int, default=288)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--reference-only', action='store_true')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
torch.set_num_threads(8)
torch.backends.mha.set_fastpath_enabled(False)
checkpoint = '/mnt/d/_work/models/m2f_swinl_mapillary'
config = Mask2FormerConfig.from_pretrained(checkpoint, local_files_only=True)
config.disable_custom_kernels = True
print('LOAD', flush=True)
model, loading = Mask2FormerForUniversalSegmentation.from_pretrained(
    checkpoint, config=config, local_files_only=True, output_loading_info=True)
if loading['missing_keys'] or loading['mismatched_keys']:
    raise RuntimeError('checkpoint did not load exactly: ' + str(loading))
model = model.eval()
if args.reference_only:
    for stage, module in model.model.pixel_level_module.encoder.hidden_states_norms.items():
        module.register_forward_hook(lambda mod, inputs, output, name=stage: np.save(args.output/('hf-'+name+'.npy'), output.detach().numpy()))


class Wrapper(nn.Module):
    def __init__(self, model, height, width):
        super().__init__()
        self.model = model
        self.height, self.width = height, width
        self.register_buffer('mean', torch.tensor([123.675, 116.280, 103.530]).view(1, 3, 1, 1))
        self.register_buffer('std', torch.tensor([58.395, 57.120, 57.375]).view(1, 3, 1, 1))

    def forward(self, image):
        output = self.model(pixel_values=(image - self.mean) / self.std,
                            output_auxiliary_logits=False, return_dict=True)
        if args.reference_only:
            np.save(args.output/'hf-class-logits.npy',output.class_queries_logits.detach().numpy())
            np.save(args.output/'hf-mask-logits.npy',output.masks_queries_logits.detach().numpy())
        classes = output.class_queries_logits.softmax(-1)[..., :-1]
        # Match the original Detectron2 model: resize mask logits before the
        # sigmoid and class projection. Sigmoid does not commute with resize.
        masks = F.interpolate(output.masks_queries_logits, size=(self.height, self.width), mode='bilinear', align_corners=False).sigmoid()
        scores = classes.transpose(1, 2) @ masks.flatten(2)
        scores = scores.reshape(1, classes.shape[-1], masks.shape[-2], masks.shape[-1])
        scores = F.interpolate(scores, size=(self.height, self.width), mode='bilinear', align_corners=False)[0]
        confidence = scores.max(0).values
        scaled = (confidence * 255).floor()
        confidence = (scaled - (scaled / 256).floor() * 256) / 255
        return scores.argmax(0).to(torch.int32), confidence.to(torch.float16)


raw = np.fromfile('/mnt/d/npudaomang/evaluation-input.f32', np.float32).reshape(1, 3, 480, 640)
y = np.arange(args.height) * 480 // args.height
x = np.arange(args.width) * 640 // args.width
small = np.ascontiguousarray(raw[:, :, y[:, None], x[None, :]])
small.astype(np.float16).tofile(args.output / 'input.f16')
image = torch.from_numpy(small)
wrapper = Wrapper(model, args.height, args.width).eval()
started = time.perf_counter()
with torch.no_grad():
    print('REFERENCE', flush=True)
    mask, confidence = wrapper(image)
    np.save(args.output / 'torch-mask.npy', mask.numpy())
    np.save(args.output / 'torch-confidence.npy', confidence.numpy())
    if not args.reference_only:
        print('EXPORT', tuple(image.shape), flush=True)
        torch.onnx.export(wrapper, (image,), str(args.output / 'swinl-fp32.onnx'),
                          input_names=['image'], output_names=['semantic_mask', 'semantic_confidence'],
                          opset_version=17, dynamo=False, do_constant_folding=True)
report = dict(width=args.width, height=args.height, checkpoint=checkpoint,
              parameters=sum(p.numel() for p in model.parameters()),
              seconds=time.perf_counter()-started,
              classes=np.unique(mask.numpy()).tolist(),
              finite_confidence=bool(np.isfinite(confidence.numpy()).all()))
(args.output / 'export.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
