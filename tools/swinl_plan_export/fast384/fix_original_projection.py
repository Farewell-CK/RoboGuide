"""Correct the missing class/query transpose in a separate 640x480 model."""
from pathlib import Path
import onnx
from onnx import helper
source=Path(r'D:\codex\swinl-npu-20261023\full-fp16\swinl-fp16-qkv-rank4-spatial-rank4.onnx')
model=onnx.load(source)
nodes=[]
fixed=0
for node in model.graph.node:
    if node.name == '/model/Einsum/ReshapeA':
        transpose=node.input[0]+'/class_query_transpose'
        nodes.append(helper.make_node('Transpose',[node.input[0]],[transpose],name='app/CorrectClassQueryOrder',perm=[1,0]))
        model.graph.value_info.append(helper.make_tensor_value_info(transpose,10,[65,100]))
        node.input[0]=transpose
        fixed+=1
    nodes.append(node)
assert fixed == 1
model.graph.ClearField('node')
model.graph.node.extend(nodes)
onnx.checker.check_model(model)
onnx.save(model,'fast384/swinl-640-projection-corrected.onnx')
print('Corrected one transpose; original model preserved.')
