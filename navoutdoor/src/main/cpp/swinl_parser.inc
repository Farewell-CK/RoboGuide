    uint32_t bufferCount = reader.get<uint32_t>();
    if (bufferCount == 0 || bufferCount > 4096) throw std::runtime_error("plan buffer count guard");
    bufferDescs.resize(bufferCount);
    buffers = std::vector<SharedBuffer>(bufferCount);
    externalInput.assign(bufferCount, false);
    for (uint32_t i = 0; i < bufferCount; i++) {
        bufferDescs[i].name = reader.text();
        bufferDescs[i].type = reader.get<int32_t>();
        uint32_t rank = reader.get<uint32_t>();
        if (rank > 6) throw std::runtime_error("plan buffer rank above six");
        bufferDescs[i].dims.resize(rank);
        for (uint32_t j = 0; j < rank; j++) bufferDescs[i].dims[j] = reader.get<uint32_t>();
    }

    if (version == 2u) {
        uint32_t initializers = reader.get<uint32_t>();
        if (initializers > bufferCount) throw std::runtime_error("static input count guard");
        for (uint32_t i = 0; i < initializers; i++) {
            uint32_t id = reader.get<uint32_t>();
            uint64_t bytes = reader.get<uint64_t>();
            if (id >= bufferCount || externalInput[id] || bytes != byte_size(bufferDescs[id].type, bufferDescs[id].dims))
                throw std::runtime_error("static input size/index mismatch");
            buffers[id].resize(bytes);
            reader.raw(buffers[id].data(), bytes);
            externalInput[id] = true;
        }
    }
    uint32_t subgraphCount = reader.get<uint32_t>();
    if (subgraphCount == 0 || subgraphCount > 8192) throw std::runtime_error("plan subgraph count guard");
    subgraphs.resize(subgraphCount);
    for (uint32_t s = 0; s < subgraphCount; s++) {
        Subgraph& sub = subgraphs[s];
        uint32_t valueCount = reader.get<uint32_t>();
        if (valueCount == 0 || valueCount > 200000) throw std::runtime_error("plan value count guard");
        sub.values.resize(valueCount);
        for (uint32_t v = 0; v < valueCount; v++) {
            ValueDesc& value = sub.values[v];
            value.type = reader.get<int32_t>();
            uint32_t rank = reader.get<uint32_t>();
            if (rank > 6) throw std::runtime_error("plan value rank above six");
            value.dims.resize(rank);
            for (uint32_t j = 0; j < rank; j++) value.dims[j] = reader.get<uint32_t>();
            uint64_t length = reader.get<uint64_t>();
            if (length > 1024ULL * 1024 * 1024) throw std::runtime_error("plan constant guard");
            value.data.resize(static_cast<size_t>(length));
            reader.raw(value.data.data(), value.data.size());
            value.binding = reader.get<uint32_t>();
            if (value.binding != 0xFFFFFFFFu && value.binding >= bufferCount) throw std::runtime_error("plan binding guard");
        }
        uint32_t opCount = reader.get<uint32_t>();
        if (opCount == 0 || opCount > 200000) throw std::runtime_error("plan op count guard");
        sub.ops.resize(opCount);
        for (uint32_t o = 0; o < opCount; o++) {
            OpDesc& op = sub.ops[o];
            op.code = reader.get<int32_t>();
            uint32_t nin = reader.get<uint32_t>();
            if (nin > 64) throw std::runtime_error("plan op input guard");
            op.in.resize(nin);
            for (uint32_t j = 0; j < nin; j++) {
                op.in[j] = reader.get<uint32_t>();
                if (op.in[j] >= valueCount) throw std::runtime_error("plan op input index");
            }
            uint32_t nout = reader.get<uint32_t>();
            if (nout == 0 || nout > 4) throw std::runtime_error("plan op output guard");
            op.out.resize(nout);
            for (uint32_t j = 0; j < nout; j++) {
                op.out[j] = reader.get<uint32_t>();
                if (op.out[j] >= valueCount) throw std::runtime_error("plan op output index");
            }
        }
        uint32_t nin = reader.get<uint32_t>();
        if (nin > 64) throw std::runtime_error("plan subgraph input guard");
        sub.inputs.resize(nin);
        for (uint32_t j = 0; j < nin; j++) {
            sub.inputs[j] = reader.get<uint32_t>();
            if (sub.inputs[j] >= valueCount) throw std::runtime_error("plan subgraph input index");
        }
        uint32_t nout = reader.get<uint32_t>();
        if (nout == 0 || nout > 256) throw std::runtime_error("plan subgraph output guard");
        sub.outputs.resize(nout);
        for (uint32_t j = 0; j < nout; j++) {
            sub.outputs[j] = reader.get<uint32_t>();
            if (sub.outputs[j] >= valueCount) throw std::runtime_error("plan subgraph output index");
        }
    }

    uint32_t resultCount = reader.get<uint32_t>();
    if (resultCount > bufferCount) throw std::runtime_error("plan result count guard");
    resultBuffers.resize(resultCount);
    for (uint32_t i = 0; i < resultCount; i++) {
        resultBuffers[i] = reader.get<uint32_t>();
        if (resultBuffers[i] >= bufferCount) throw std::runtime_error("plan result index");
    }
