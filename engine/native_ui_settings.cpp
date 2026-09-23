// Narrow adapter to the installed MMKV library: one subdraft performance flag.
#include <string>
#include <iostream>
#include <stdexcept>
namespace mmkvcpp {
enum MMKVMode { SINGLE_PROCESS=1 };
enum MMKVLogLevel { None=4 };
enum SyncFlag { SYNC=1 };
class MMKV { public:
 static void initializeMMKV(const std::string&, MMKVLogLevel);
 static MMKV* mmkvWithID(const std::string&, MMKVMode, std::string*, std::string*);
 bool getString(const std::string&, std::string&);
 bool set(const std::string&, const std::string&);
 void sync(SyncFlag); void close();
};
}
int main(int argc, char** argv) {
 try {
  if (argc!=3) throw std::runtime_error("usage: settings-helper root read|disable|enable");
  const std::string mode=argv[2];
  if (mode!="read" && mode!="disable" && mode!="enable") throw std::runtime_error("unsupported action");
  mmkvcpp::MMKV::initializeMMKV(argv[1],mmkvcpp::None);
  auto kv=mmkvcpp::MMKV::mmkvWithID("settings_json",mmkvcpp::SINGLE_PROCESS,nullptr,nullptr);
  if (!kv) throw std::runtime_error("settings unavailable");
  std::string value;
  if (!kv->getString("key_settings_json",value)) throw std::runtime_error("settings value unavailable");
  const std::string key="\"sub_draft_async_load\":", group="\"draft_load_optimize_part1\":{";
  auto start=value.find(group), pos=value.find(key);
  if (start==std::string::npos || pos==std::string::npos || value.find(key,pos+key.size())!=std::string::npos ||
      pos<start+group.size() || pos>value.find('}',start)) throw std::runtime_error("unexpected settings shape");
  pos+=key.size(); bool old;
  if (value.compare(pos,4,"true")==0) old=true;
  else if (value.compare(pos,5,"false")==0) old=false;
  else throw std::runtime_error("flag is not boolean");
  bool wanted=mode=="read"?old:mode=="enable";
  if (wanted!=old) {
   value.replace(pos,old?4:5,wanted?"true":"false");
   if (!kv->set(value,"key_settings_json")) throw std::runtime_error("settings write failed");
   kv->sync(mmkvcpp::SYNC);
   std::string check;
   if (!kv->getString("key_settings_json",check) || check!=value) throw std::runtime_error("settings verification failed");
  }
  kv->close();
  std::cout<<"{\"previous_async\":"<<(old?"true":"false")<<",\"async_enabled\":"<<(wanted?"true":"false")<<"}\n";
  return 0;
 } catch (const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
