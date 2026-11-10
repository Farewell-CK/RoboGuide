#include <jni.h>
#include <dlfcn.h>
#include <elf.h>
#include <link.h>
#include <fstream>
#include <string>
#include <array>
#include <stdexcept>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <cstdint>
#include <unordered_set>
#include <sstream>
#include <iomanip>

// Private offsets below are valid ONLY for the SHA-256-pinned binary checked by Java.
// Independently verify the loaded ELF build-id before reading them. Never mutate either tree.
namespace {
constexpr const char* writeName="_ZNK7octomap14AbstractOcTree5writeERKNSt6__ndk112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE";
constexpr const char* readName="_ZN7octomap14AbstractOcTree4readERKNSt6__ndk112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE";
constexpr const char* sizeName="_ZNK7octomap14OcTreeBaseImplINS_19SemanticsOcTreeNodeINS_12SemanticsMaxEEENS_23AbstractOccupancyOcTreeEE4sizeEv";
constexpr std::array<uint8_t,32> librarySha={0xcf,0x60,0x06,0xb3,0xdd,0x36,0x67,0x88,0xa8,0x8f,0xeb,0xe9,0x94,0x30,0x2b,0xe7,0x7d,0x43,0x1c,0x62,0xd5,0x01,0xfe,0xab,0xea,0xa5,0x8a,0x34,0x92,0x61,0xed,0x61};
constexpr std::array<uint8_t,20> buildId={0x7b,0xf7,0x5c,0x29,0xa7,0xba,0x8e,0x49,0x05,0x04,0x44,0xdb,0xb3,0x68,0x67,0xb0,0xbc,0x48,0x7a,0x0d};
constexpr uint64_t maxNodes=2000000;
using Write=bool(*)(const void*,const std::string&);
using Read=void*(*)(const std::string&);
using Destroy=void(*)(JNIEnv*,jclass,jlong);
using Count=jint(*)(JNIEnv*,jclass,jlong);
using Size=size_t(*)(const void*);

template<typename T>T load(const void* base,size_t offset){T value;std::memcpy(&value,static_cast<const uint8_t*>(base)+offset,sizeof(T));return value;}
struct IdentityCheck {uintptr_t base;bool match=false;};
int checkElf(struct dl_phdr_info* info,size_t,void* opaque) {
    auto& check=*static_cast<IdentityCheck*>(opaque);
    if(static_cast<uintptr_t>(info->dlpi_addr)!=check.base)return 0;
    for(size_t i=0;i<info->dlpi_phnum;i++) {
        const auto& segment=info->dlpi_phdr[i];if(segment.p_type!=PT_NOTE)continue;
        const uint8_t* bytes=reinterpret_cast<const uint8_t*>(info->dlpi_addr+segment.p_vaddr);size_t cursor=0;
        while(cursor+sizeof(Elf64_Nhdr)<=segment.p_memsz) {
            Elf64_Nhdr note;std::memcpy(&note,bytes+cursor,sizeof(note));cursor+=sizeof(note);
            size_t nameSize=(static_cast<size_t>(note.n_namesz)+3)&~size_t(3),descSize=(static_cast<size_t>(note.n_descsz)+3)&~size_t(3);
            if(nameSize>segment.p_memsz-cursor||descSize>segment.p_memsz-cursor-nameSize)break;
            if(note.n_type==NT_GNU_BUILD_ID&&note.n_namesz==4&&std::memcmp(bytes+cursor,"GNU\0",4)==0&&note.n_descsz==buildId.size())
                check.match=std::memcmp(bytes+cursor+nameSize,buildId.data(),buildId.size())==0;
            cursor+=nameSize+descSize;
        }
    }
    return 1;
}
void requireIdentity(void* symbol) {
    Dl_info info{};if(!dladdr(symbol,&info)||!info.dli_fbase)throw std::runtime_error("Cannot identify loaded OctoMap library");
    IdentityCheck identity{reinterpret_cast<uintptr_t>(info.dli_fbase),false};dl_iterate_phdr(checkElf,&identity);
    if(!identity.match)throw std::runtime_error("Loaded OctoMap ELF build-id does not match pinned adapter");
}
struct TreeInfo {const void* root;uint32_t depth;uint64_t nodes,leaves,resolutionBits;};
double resolutionValue(const TreeInfo&info){double value;std::memcpy(&value,&info.resolutionBits,8);return value;}
std::string headerDescription(const TreeInfo&info) {
    std::ostringstream text;
    text<<"depth="<<info.depth<<",nodes="<<info.nodes<<",leaves="<<info.leaves
        <<",resolution="<<std::setprecision(17)<<resolutionValue(info)
        <<",resolution_bits=0x"<<std::hex<<info.resolutionBits;
    return text.str();
}
void verifyReadbackHeader(const TreeInfo&before,const TreeInfo&after) {
    // The pinned .ot writer inserts the resolution into a fresh ostream with its
    // default six significant decimal digits (AbstractOcTree::write at 0x34488).
    // Float-origin 0.20000000298023224 therefore reads back as double 0.2. The
    // authoritative .sem header keeps all original double bits; tolerate ONLY
    // the known text-header quantization for this compatibility .ot check.
    const double expected=resolutionValue(before),actual=resolutionValue(after);
    const double scale=std::pow(10.0,std::floor(std::log10(std::fabs(expected)))-5.0);
    const double tolerance=0.5*scale+std::fabs(expected)*1e-14;
    const bool resolutionMatches=std::isfinite(actual)&&std::fabs(expected-actual)<=tolerance;
    if(before.depth!=after.depth||before.nodes!=after.nodes||before.leaves!=after.leaves||!resolutionMatches) {
        std::ostringstream error;
        error<<"Readback tree header mismatch: expected{"<<headerDescription(before)
             <<"}, actual{"<<headerDescription(after)<<"}, resolution_tolerance="
             <<std::setprecision(17)<<tolerance;
        throw std::runtime_error(error.str());
    }
}
TreeInfo describe(const void* tree,Size size,Count count,JNIEnv*env) {
    TreeInfo info{load<const void*>(tree,0x20),load<uint32_t>(tree,0x28),size(tree),static_cast<uint64_t>(count(env,nullptr,reinterpret_cast<jlong>(tree))),load<uint64_t>(tree,0x30)};
    double resolution;std::memcpy(&resolution,&info.resolutionBits,8);
    if(info.depth!=16||info.nodes>maxNodes||info.leaves>info.nodes||!std::isfinite(resolution)||resolution<=0||resolution>10||((info.root==nullptr)!=(info.nodes==0)))
        throw std::runtime_error("Unsupported or inconsistent OctoMap tree layout");
    return info;
}
uint8_t childMask(const void* node) {
    const void* children=load<const void*>(node,0);if(!children)return 0;
    if(reinterpret_cast<uintptr_t>(children)%alignof(void*))throw std::runtime_error("Unaligned node child array");
    uint8_t mask=0;for(unsigned i=0;i<8;i++)if(load<const void*>(children,i*sizeof(void*)))mask|=1u<<i;return mask;
}
struct WalkState {std::unordered_set<const void*> visited;uint64_t leaves=0;};
template<typename F>void walkNode(const void* node,unsigned depth,uint64_t path,const TreeInfo&tree,WalkState&state,F&visit) {
    if(!node||reinterpret_cast<uintptr_t>(node)%alignof(void*)||depth>tree.depth||!state.visited.insert(node).second||state.visited.size()>tree.nodes)
        throw std::runtime_error("Invalid or repeated OctoMap node");
    uint8_t mask=childMask(node);if(depth==tree.depth&&mask)throw std::runtime_error("Node children exceed tree depth");
    if(!mask)state.leaves++;
    visit(node,static_cast<uint8_t>(depth),path,mask);
    const void* children=load<const void*>(node,0);
    for(unsigned i=0;i<8;i++)if(mask&(1u<<i))walkNode(load<const void*>(children,i*sizeof(void*)),depth+1,(path<<3)|i,tree,state,visit);
}
template<typename F>void walk(const TreeInfo&tree,F visit) {
    WalkState state;state.visited.reserve(static_cast<size_t>(tree.nodes));
    if(tree.root)walkNode(tree.root,0,0,tree,state,visit);
    if(state.visited.size()!=tree.nodes||state.leaves!=tree.leaves)throw std::runtime_error("OctoMap traversal counts differ from native tree");
}
void writeNumber(std::ostream&out,uint64_t value,unsigned bytes){for(unsigned i=0;i<bytes;i++)out.put(static_cast<char>((value>>(i*8))&255));}
uint64_t readNumber(std::istream&in,unsigned bytes){uint64_t value=0;for(unsigned i=0;i<bytes;i++){int byte=in.get();if(byte==EOF)throw std::runtime_error("Truncated semantic sidecar");value|=static_cast<uint64_t>(static_cast<uint8_t>(byte))<<(i*8);}return value;}
void expectNumber(std::istream&in,uint64_t value,unsigned bytes){if(readNumber(in,bytes)!=value)throw std::runtime_error("Semantic sidecar field mismatch");}
void writeHeader(std::ostream&out,const TreeInfo&t){out.write("NSEM0001",8);writeNumber(out,1,4);writeNumber(out,t.depth,4);writeNumber(out,t.resolutionBits,8);writeNumber(out,t.nodes,8);writeNumber(out,t.leaves,8);writeNumber(out,25,4);out.write(reinterpret_cast<const char*>(librarySha.data()),librarySha.size());out.write(reinterpret_cast<const char*>(buildId.data()),buildId.size());}
void verifyHeader(std::istream&in,const TreeInfo&t){std::array<char,8> magic{};in.read(magic.data(),magic.size());if(!in||std::memcmp(magic.data(),"NSEM0001",8))throw std::runtime_error("Invalid semantic sidecar header");expectNumber(in,1,4);expectNumber(in,t.depth,4);expectNumber(in,t.resolutionBits,8);expectNumber(in,t.nodes,8);expectNumber(in,t.leaves,8);expectNumber(in,25,4);for(uint8_t b:librarySha)expectNumber(in,b,1);for(uint8_t b:buildId)expectNumber(in,b,1);}
void writeSidecar(const TreeInfo&t,const std::string&filename) {
    std::ofstream out(filename,std::ios::binary|std::ios::trunc);if(!out)throw std::runtime_error("Cannot create semantic sidecar");
    writeHeader(out,t);
    walk(t,[&](const void*node,uint8_t depth,uint64_t path,uint8_t mask){
        writeNumber(out,depth,1);writeNumber(out,path,8);writeNumber(out,mask,1);writeNumber(out,load<uint32_t>(node,8),4);
        for(size_t offset:{12,13,14,16,17,18})writeNumber(out,load<uint8_t>(node,offset),1);
        writeNumber(out,load<uint32_t>(node,20),4);uint8_t flag=load<uint8_t>(node,24);if(flag>1)throw std::runtime_error("Invalid native semantic display flag");writeNumber(out,flag,1);
    });
    out.flush();if(!out)throw std::runtime_error("Semantic sidecar write failed");out.close();
    // Parse the sidecar and compare EVERY field with the unchanged original, including NaN bits.
    std::ifstream in(filename,std::ios::binary);verifyHeader(in,t);
    walk(t,[&](const void*node,uint8_t depth,uint64_t path,uint8_t mask){
        expectNumber(in,depth,1);expectNumber(in,path,8);expectNumber(in,mask,1);expectNumber(in,load<uint32_t>(node,8),4);
        for(size_t offset:{12,13,14,16,17,18})expectNumber(in,load<uint8_t>(node,offset),1);
        expectNumber(in,load<uint32_t>(node,20),4);expectNumber(in,load<uint8_t>(node,24),1);
    });
    if(in.peek()!=EOF)throw std::runtime_error("Unexpected trailing semantic sidecar bytes");
}
void compareOccupancy(const void*a,const void*b,unsigned depth,const TreeInfo&t) {
    if(depth>t.depth||!a||!b)throw std::runtime_error("Readback tree topology mismatch");
    uint8_t mask=childMask(a);if(mask!=childMask(b)||load<uint32_t>(a,8)!=load<uint32_t>(b,8))throw std::runtime_error("Readback occupancy or topology mismatch");
    const void*ac=load<const void*>(a,0);const void*bc=load<const void*>(b,0);
    for(unsigned i=0;i<8;i++)if(mask&(1u<<i))compareOccupancy(load<const void*>(ac,i*sizeof(void*)),load<const void*>(bc,i*sizeof(void*)),depth+1,t);
}
}
extern "C" JNIEXPORT jint JNICALL Java_com_elabrador_mobilenavigation_NativeMapArchive_nativeSaveVerifiedPinned(JNIEnv*env,jclass,jlong handle,jstring path) {
    void*library=dlopen("libelabrador_native.so",RTLD_NOW|RTLD_LOCAL);
    if(!library){env->ThrowNew(env->FindClass("java/io/IOException"),"OctoMap library unavailable");return -1;}
    void* restored=nullptr;Destroy destroy=nullptr;std::string sidecar;
    try {
        void* writeSymbol=dlsym(library,writeName);requireIdentity(writeSymbol);
        auto write=reinterpret_cast<Write>(writeSymbol);auto read=reinterpret_cast<Read>(dlsym(library,readName));auto size=reinterpret_cast<Size>(dlsym(library,sizeName));
        destroy=reinterpret_cast<Destroy>(dlsym(library,"Java_com_elabrador_mobilenavigation_NativeOctomap_nativeDestroy"));
        auto count=reinterpret_cast<Count>(dlsym(library,"Java_com_elabrador_mobilenavigation_NativeOctomap_nativeLeafCount"));
        if(!write||!read||!destroy||!count||!size||!handle)throw std::runtime_error("Unsupported OctoMap serialization ABI");
        const char*utf=env->GetStringUTFChars(path,nullptr);if(!utf){dlclose(library);return -1;}
        std::string filename(utf);env->ReleaseStringUTFChars(path,utf);sidecar=filename+".sem";
        const void* original=reinterpret_cast<const void*>(handle);TreeInfo before=describe(original,size,count,env);
        writeSidecar(before,sidecar);
        if(!write(original,filename))throw std::runtime_error("OctoMap compatibility serialization failed");
        restored=read(filename);if(!restored)throw std::runtime_error("OctoMap compatibility readback failed");
        TreeInfo after=describe(restored,size,count,env);
        verifyReadbackHeader(before,after);
        // The shipped .ot writer omits confidence and stores only one RGB. Compare only what it preserves.
        if(before.root)compareOccupancy(before.root,after.root,0,before);
        jint result=static_cast<jint>(before.leaves);destroy(env,nullptr,reinterpret_cast<jlong>(restored));restored=nullptr;
        dlclose(library);return result;
    }catch(const std::exception&error) {
        if(restored&&destroy)destroy(env,nullptr,reinterpret_cast<jlong>(restored));
        if(!sidecar.empty())std::remove(sidecar.c_str());dlclose(library);
        env->ThrowNew(env->FindClass("java/io/IOException"),error.what());return -1;
    }
}
