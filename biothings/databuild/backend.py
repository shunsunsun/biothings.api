'''
Backend for storing merged genedoc after building.
Support MongoDB, ES, CouchDB
'''
from functools import partial
from biothings.utils.common import get_timestamp, get_random_string
from biothings.utils.backend import DocBackendBase, DocMongoBackend, DocESBackend
import biothings.utils.mongo as mongo

# Source specific backend (deals with build config, master docs, etc...)
class SourceDocBackendBase(DocBackendBase):

    def __init__(self, build, master, dump, sources):
        if type(build) == partial:
            self._build_provider = build
            self._build = None
        else:
            self._build_provider = None
            self._build = build
        if type(master) == partial:
            self._master_provider = master
            self._master = None
        else:
            self._master_provider = None
            self._master = master
        if type(dump) == partial:
            self._dump_provider = dump
            self._dump = None
        else:
            self._dump_provider = None
            self._dump = dump
        if type(sources) == partial:
            self._sources_provider = sources
            self._sources = None
        else:
            self._sources_provider = None
            self._sources = sources
        self._build_config = None
        self.src_masterdocs = None
        # keep track of sources which were accessed
        self.sources_accessed = {}

    def __getattr__(self,attr):
        if attr not in ["build","dump","master","sources"]:
            return AttributeError(attr)
        privattr = "_" + attr
        if getattr(self,privattr) is None:
            if getattr(self,privattr + "_provider"):
                print("onici")
                res = getattr(self,privattr + "_provider")()
            else:
                print("onlea")
                res = getattr(self,privattr)
            setattr(self,privattr,res)
        return getattr(self,privattr)

    def get_build_configuration(self, build):
        raise NotImplementedError("sub-class and implement me")

    def get_src_master_docs(self):
        raise NotImplementedError("sub-class and implement me")

    def validate_sources(self,sources=None):
        raise NotImplementedError("sub-class and implement me")

    def get_src_versions(self):
        raise NotImplementedError("sub-class and implement me")

    def __getitem__(self,src_name):
        self.sources_accessed[src_name] = 1
        return self.sources[src_name]


# Target specific backend
class TargetDocBackend(DocBackendBase):

    def __init__(self,*args,**kwargs):
        super(TargetDocBackend,self).__init__(*args,**kwargs)
        self.target_name = None

    def set_target_name(self,target_name, build_name=None):
        """
        Create/prepare a target backend, either strictly named "target_name"
        or named derived from "build_name" (for temporary backends)
        """
        self.target_name = target_name or self.generate_target_name(build_name)

    def generate_target_name(self,build_config_name):
        assert not build_config_name is None
        return '{}_{}_{}'.format(build_config_name,
                                         get_timestamp(), get_random_string()).lower()

    def post_merge(self):
        pass

class SourceDocMongoBackend(SourceDocBackendBase):

    def get_build_configuration(self, build):
        self._build_config = self.build.find_one({'_id' : build})
        return self._build_config

    def validate_sources(self,sources=None):
        assert self._build_config, "'self._build_config' cannot be empty."

    def get_src_master_docs(self):
        if self.src_masterdocs is None:
            self.src_masterdocs = dict([(src['_id'], src) for src in list(self.master.find())])
        return self.src_masterdocs

    def get_src_versions(self,src_filter=[]):
        """
        Return source versions which have been previously accessed wit this backend object
        or all source versions if none were accessed. Accessing means going through __getitem__
        (the usual way) and allows to auto-keep track of sources of interest, thus returning
        versions only for those.
        src_filter can be passed (list of source _id) to add a filter step.
        """
        src_version = {}
        srcs = []
        if self.sources_accessed:
            for src in self.sources_accessed:
                doc = self.dump.find_one({"$where":"function() {for(var index in this.upload.jobs) {if(this.upload.jobs[index].step == \"%s\") return this;}}" % src})
                srcs.append(doc["_id"])
            srcs = list(set(srcs))
        else:
            srcs = [d["_id"] for d in self.dump.find()]
        # we need to return main_source named, but if accessed, it's been through sub-source names
        # query is different in that case
        if src_filter:
            srcs = list(set(srcs).intersection(set(src_filter)))
        for src in self.dump.find({"_id":{"$in":srcs}}):
            version = src.get('release', src.get('timestamp', None))
            if version:
                src_version[src['_id']] = version
        return src_version


class TargetDocMongoBackend(TargetDocBackend,DocMongoBackend):

    def set_target_name(self,target_name=None, build_name=None):
        super(TargetDocMongoBackend,self).set_target_name(target_name,build_name)
        self.target_collection = self.target_db[self.target_name]


def create_backend(db_col_names,name_only=False):
    """
    Guess what's inside 'db_col_names' and return the corresponding collection.
    It could be a string (by default, will lookup a collection in target database)
    or a tuple("targe$t|src","col_name") or even a ("mongodb://user:pass","db","col_name") URI.
    If name_only is true, just return the name of the collection
    """
    col = None
    db = None
    if type(db_col_names) == str:
        if name_only:
            col = db_col_names
        else:
            db = mongo.get_target_db()
            col = db[db_col_names]
    elif db_col_names[0].startswith("mongodb://"):
        assert len(db_col_names) == 3, "Missing connection information for %s" % repr(db_col_names)
        if name_only:
            col = db_col_names[2]
        else:
            conn = mongo.MongoClient(db_col_names[0])
            db = conn[db_col_names[1]]
            col = db[db_col_names[2]]
    else:
        assert len(db_col_names) == 2, "Missing connection information for %s" % repr(db_col_names)
        if name_only:
            col = db_col_names[1]
        else:
            db = db_col_names[0] == "target" and mongo.get_target_db() or mongo.get_src_db()
            col = db[db_col_names[1]]
    assert not col is None, "Could not create collection object from %s" % repr(db_col_names)
    if name_only:
        return col
    else:
        return DocMongoBackend(db,col)
