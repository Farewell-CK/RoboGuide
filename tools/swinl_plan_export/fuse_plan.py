"""Fuse adjacent NNAPI models while preserving aliased buffer views and IO."""
import numpy as np
import nnapi as n

def merge(plan, group_size=2, max_ops=160, max_elements=8000000, max_inputs=60, forced_groups=None):
    if group_size<=1:return dict(before=len(plan.subs),after=len(plan.subs))
    readers={}
    for si,sub in enumerate(plan.subs):
        for value in sub.inputs:
            b=sub.values[value][3]
            readers.setdefault(b,set()).add(si)
    original=list(plan.subs)
    fused=[]
    groups=[]
    start=0
    def large(sub):
        return getattr(sub,'no_fusion',False) or (max_elements>0 and any(np.prod(sub.values[o][1])>=max_elements for _,_,outs in sub.ops for o in outs))
    def input_count(start,end):
        produced=set();inputs=set()
        for old in original[start:end]:
            for vi in old.inputs:
                dtype,dims,_,binding=old.values[vi]
                if binding not in produced:inputs.add((binding,dtype,tuple(dims)))
            produced.update(old.values[v][3] for v in old.outputs)
        return len(inputs)
    while start<len(original):
        end=start+1
        count=len(original[start].ops)
        while end<len(original) and end-start<group_size:
            if large(original[start]) or large(original[end]):break
            if count+len(original[end].ops)>max_ops:break
            # Keep large sampling models separate to bound compiler workspace.
            if len(original[end].ops)>max_ops:break
            if input_count(start,end+1)>max_inputs:break
            count+=len(original[end].ops);end+=1
        groups.append((start,end));start=end
    if forced_groups is not None:
        if not forced_groups or forced_groups[0][0]!=0 or forced_groups[-1][1]!=len(original):raise ValueError('Incomplete forced fusion partition')
        if any(a>=b or (i and forced_groups[i-1][1]!=a) for i,(a,b) in enumerate(forced_groups)):raise ValueError('Invalid forced fusion partition')
        groups=forced_groups
    for start,end in groups:
        if end-start==1:
            fused.append(original[start]);continue
        sub=n.Sub(plan)
        produced={}
        input_values={}
        constants={}
        output_bindings={}
        def constant(dtype,dims,data):
            key=(dtype,tuple(dims),data)
            if key not in constants:constants[key]=sub.val(dtype,dims,const=data)
            return constants[key]
        def i32(values):return constant(n.TENSOR_INT32,(len(values),),np.asarray(values,np.int32).tobytes())
        for si in range(start,end):
            old=original[si]
            remap={}
            for vi,(dtype,dims,data,binding) in enumerate(old.values):
                if data is not None:
                    remap[vi]=constant(dtype,dims,data)
                elif vi in old.inputs:
                    if binding in produced:
                        prev=produced[binding]
                        pdims=sub.values[prev][1]
                        if tuple(pdims)==tuple(dims):remap[vi]=prev
                        else:
                            # A host buffer alias is a byte-preserving reshape.
                            assert np.prod(pdims)==np.prod(dims)
                            v=sub.val(dtype,dims)
                            sub.op(n.RESHAPE,[prev,i32(dims)],v);remap[vi]=v
                    else:
                        key=(binding,dtype,tuple(dims))
                        if key not in input_values:
                            input_values[key]=sub.val(dtype,dims,binding=binding,as_input=True)
                        remap[vi]=input_values[key]
                else:remap[vi]=sub.val(dtype,dims)
            output_set=set(old.outputs)
            for code,ins,outs in old.ops:
                # Boundary identity copies become unnecessary inside one model.
                identity=None
                if len(outs)==1 and outs[0] in output_set:
                    if code==n.ADD:
                        for slot in (0,1):
                            v=old.values[ins[slot]]
                            if v[0]==n.TENSOR_FLOAT16 and v[2] is not None and not np.frombuffer(v[2],np.float16).any():
                                if np.frombuffer(old.values[ins[2]][2],np.int32)[0]==0:
                                    identity=remap[ins[1-slot]];break
                    elif code==n.RESHAPE and old.values[ins[0]][1]==old.values[outs[0]][1]:identity=remap[ins[0]]
                if identity is not None:remap[outs[0]]=identity
                else:sub.op(code,[remap[v] for v in ins],[remap[v] for v in outs])
            for vi in old.outputs:
                binding=old.values[vi][3]
                produced[binding]=remap[vi]
                if binding in plan.results or any(s>=end for s in readers.get(binding,())):
                    output_bindings[binding]=remap[vi]
        # Fresh output copies avoid MDLA's output-also-consumed restriction.
        for binding,source in output_bindings.items():
            dtype,dims,_,_=sub.values[source]
            output=sub.val(dtype,dims,binding=binding)
            if dtype==n.TENSOR_FLOAT16:
                zero=constant(dtype,(1,)*len(dims),np.float16(0).tobytes())
                fuse=constant(n.INT32,(),np.int32(0).tobytes())
                sub.op(n.ADD,[source,zero,fuse],output)
            else:sub.op(n.RESHAPE,[source,i32(dims)],output)
            sub.out(output)
        # Drop abandoned copies and any constants they alone referenced.
        used=set(sub.inputs+sub.outputs)
        used.update(v for _,ins,outs in sub.ops for v in ins+outs)
        order=sorted(used);mapping={v:i for i,v in enumerate(order)}
        sub.values=[sub.values[v] for v in order]
        sub.ops=[(c,[mapping[v] for v in ins],[mapping[v] for v in outs]) for c,ins,outs in sub.ops]
        sub.inputs=[mapping[v] for v in sub.inputs]
        sub.outputs=[mapping[v] for v in sub.outputs]
        fused.append(sub)
    plan.subs=fused
    return dict(before=len(original),after=len(fused),group_size=group_size,max_ops=max_ops,max_elements=max_elements,max_inputs=max_inputs)
