
# Python 2.7 Standard Library
from __future__ import absolute_import, print_function
import argparse
import collections
import inspect
import json
import sys

# Third-Party Libraries
import plumbum

# Pandoc
from .about import *
from . import utils
from . import types


# TODO / Roadmap
# ------------------------------------------------------------------------------
#
# TODO: target 2.0 milestone, that supports up to pandoc 2.0
#
#  - switch readers/writers (lazily) depending of pandoc_api_version >= 1.17
#    or not
#
#  - pandoc executable API (connect with version API)
#
#  - reader and writer for more than JSON (Markdown, HTML, etc.)
#
#  - test new JSON scheme completely (need a harness with arbitrary 
#    pandoc executable version)
#
#  - error management/messages in type checking. MAYBE ROLLBACK THIS
#    ATM (needs a great effort) and make a branch that will land in
#    3.0 ? Or 2.1 whatever ...
#
#  - documentation (mkdocs): START ! Will make the public API design
#    issues easier (maybe)
#
#  - reconsider "main"?
#


# Configuration
# ------------------------------------------------------------------------------
_configuration = None

def configure(auto=None, path=None, version=None, pandoc_types_version=None):
    global _configuration

    # Default configuration: set auto to `True`.
    if auto is None and \
       path is None and \
       version is None and \
       pandoc_types_version is None:
       auto = True

    if auto is True: 
        try:
            pandoc = plumbum.local['pandoc'] # Encoding issue? pandoc works
            # with utf-8 in and out by construction, but maybe plumbum infers
            # something different.
            found_path = str(pandoc.executable)
        except plumbum.CommandNotFound as error:
            message  = 'cannot find the pandoc program.\n'
            paths = [str(p) for p in error.path]
            message += 'paths:' + str(paths)
            raise RuntimeError(message)
        if path is None:
            path = found_path
        elif path != found_path:
            error  = 'found path {0!r} with auto=True '
            error += 'but it doesn\'t match path={1!r}.'
            raise ValueError(error.format(found_path, path))

    if path is not None:
        # if the path invalid, will fails with OSError *when called*
        pandoc = plumbum.machines.LocalCommand(path)
        found_version = pandoc('--version').splitlines()[0].split(' ')[1]
        if version is None:
            version = found_version
        elif version != found_version:
            error  = 'the version of the pandoc program is {0!r}'
            error += 'but it doesn\'t match version={1!r}.'
            raise ValueError(error.format(found_version, version))

    if version is not None:
        found_pandoc_types_versions = utils.resolve(version)
        if pandoc_types_version is None:
            if len(found_pandoc_types_versions) >= 1:
                pandoc_types_version = found_pandoc_types_versions[0]
            else:
                error  = 'cannot find a version of pandoc-types '
                error += 'matching pandoc {0}' 
                raise ValueError(error.format(version))
        elif pandoc_types_version not in found_pandoc_types_versions:
            error  = 'the version of pandoc is {0!r}'
            error += 'but it doesn\'t match pandoc_types_version={1!r}.'
            raise ValueError(error.format(version, pandoc_types_version))

    types.make_types(pandoc_types_version)

    _configuration = {
      'auto': auto, 
      'path': path, 
      'version': version, 
      'pandoc_types_version': pandoc_types_version
    }

    return _configuration


# JSON Reader / Writer
# ------------------------------------------------------------------------------
def read(*args, **kwargs):
    if utils.version_key(_configuration["pandoc_types_version"]) < [1, 17]:
        return read1(*args, **kwargs)
    else:
        return read2(*args, **kwargs)

def write(*args, **kwargs):
    if utils.version_key(_configuration["pandoc_types_version"]) < [1, 17]:
        return write1(*args, **kwargs)
    else:
        return write2(*args, **kwargs)

# JSON Reader v1
# ------------------------------------------------------------------------------
def read1(json_, type_=None):
    if type_ is None:
        type_ = types.Pandoc
    if isinstance(type_, str):
        type_ = getattr(types, type_)
    if not isinstance(type_, list): # not a type def (yet).
        if issubclass(type_, types.Type):
            type_ = type_._def
        else: # primitive type
            return type_(json_)

    if type_[0] == "type": # type alias
        type_ = type_[1][1]
        return read1(json_, type_)
    if type_[0] == "list":
        item_type = type_[1][0]
        return [read1(item, item_type) for item in json_]
    if type_[0] == "tuple":
        tuple_types = type_[1]
        return tuple(read1(item, item_type) for (item, item_type) in zip(json_, tuple_types))
    if type_[0] == "map":
        key_type, value_type = type_[1]
        return types.map([(read1(k, key_type), read1(v, value_type)) for (k, v) in json_.items()])

    data_type = None
    constructor = None
    if type_[0] in ("data", "newtype"):
        data_type = type_
        constructors = data_type[1][1]
        if len(constructors) == 1:
            constructor = constructors[0]
        else:
            constructor = getattr(types, json_["t"])._def
    elif type_[0][0] == type_[0][0].upper():
        constructor = type_
        constructor_type = getattr(types, constructor[0])
        data_type = constructor_type.__mro__[2]._def

    single_type_constructor = (len(data_type[1][1]) == 1)
    single_constructor_argument = (len(constructor[1][1]) == 1)
    is_record = (constructor[1][0] == "map")

    json_args = None
    args = None
    if not is_record:
        if single_type_constructor:
            json_args = json_
        else:
            json_args = json_["c"]
        if single_constructor_argument:
            json_args = [json_args]
        args = [read1(jarg, t) for jarg, t in zip(json_args, constructor[1][1])]
    else:
        keys = [k for k,t in constructor[1][1]]
        types_= [t for k, t in constructor[1][1]]
        json_args = [json_[k] for k in keys]
        args = [read1(jarg, t) for jarg, t in zip(json_args, types_)]
    C = getattr(types, constructor[0])
    return C(*args)


# JSON Writer v1
# ------------------------------------------------------------------------------
def write1(object_):
    odict = collections.OrderedDict
    type_ = type(object_)
    if not isinstance(object_, types.Type):
        if isinstance(object_, (list, tuple)):
            json_ = [write1(item) for item in object_]
        elif isinstance(object_, dict):
            json_ = odict((k, write1(v)) for k, v in object_.items())
        else: # primitive type
            json_ = object_
    else:
        constructor = type(object_)._def
        data_type = type(object_).__mro__[2]._def
        single_type_constructor = (len(data_type[1][1]) == 1)
        single_constructor_argument = (len(constructor[1][1]) == 1)
        is_record = (constructor[1][0] == "map")

        json_ = odict()
        if not single_type_constructor:
            json_["t"] = type(object_).__name__

        if not is_record:
            c = [write1(arg) for arg in object_]
            if single_constructor_argument:
                c = c[0]
            if single_type_constructor:
                json_ = c
            else:
                json_["c"] = c
        else:
            keys = [kt[0] for kt in constructor[1][1]]
            for key, arg in zip(keys, object_):
                json_[key] = write1(arg)
    return json_


# JSON Reader v2
# ------------------------------------------------------------------------------
def read2(json_, type_=None):
    if type_ is None:
        type_ = types.Pandoc
    if isinstance(type_, str):
        type_ = getattr(types, type_)
    if not isinstance(type_, list): # not a type def (yet).
        if issubclass(type_, types.Type):
            type_ = type_._def
        else: # primitive type
            return type_(json_)

    if type_[0] == "type": # type alias
        type_ = type_[1][1]
        return read2(json_, type_)
    if type_[0] == "list":
        item_type = type_[1][0]
        return [read2(item, item_type) for item in json_]
    if type_[0] == "tuple":
        tuple_types = type_[1]
        return tuple(read2(item, item_type) for (item, item_type) in zip(json_, tuple_types))
    if type_[0] == "map":
        key_type, value_type = type_[1]
        return types.map([(read2(k, key_type), read2(v, value_type)) for (k, v) in json_.items()])

    data_type = None
    constructor = None
    if type_[0] in ("data", "newtype"):
        data_type = type_
        constructors = data_type[1][1]
        if len(constructors) == 1:
            constructor = constructors[0]
        else:
            constructor = getattr(types, json_["t"])._def
    elif type_[0][0] == type_[0][0].upper():
        constructor = type_
        constructor_type = getattr(types, constructor[0])
        data_type = constructor_type.__mro__[2]._def

    single_type_constructor = (len(data_type[1][1]) == 1)
    single_constructor_argument = (len(constructor[1][1]) == 1)
    is_record = (constructor[1][0] == "map")

    json_args = None
    args = None
    if constructor[0] == "Pandoc":
        # TODO; check API version compat
        meta = read2(json_["meta"], types.Meta)
        blocks = read2(json_["blocks"], ["list", ["Block"]])
        return types.Pandoc(meta, blocks)
    elif constructor[0] == "Meta":
        type_ = ['map', ['String', 'MetaValue']]
        return types.Meta(read2(json_, type_)) 
    elif not is_record:
        if single_type_constructor:
            json_args = json_
        else:
            json_args = json_.get("c", [])
        if single_constructor_argument:
            json_args = [json_args]
        args = [read2(jarg, t) for jarg, t in zip(json_args, constructor[1][1])]
    else:
        keys = [k for k,t in constructor[1][1]]
        types_= [t for k, t in constructor[1][1]]
        json_args = [json_[k] for k in keys]
        args = [read2(jarg, t) for jarg, t in zip(json_args, types_)]
    C = getattr(types, constructor[0])
    return C(*args)


# JSON Writer v2
# ------------------------------------------------------------------------------
def write2(object_):
    odict = collections.OrderedDict
    type_ = type(object_)
    if not isinstance(object_, types.Type):
        if isinstance(object_, (list, tuple)):
            json_ = [write2(item) for item in object_]
        elif isinstance(object_, dict):
            json_ = odict((k, write2(v)) for k, v in object_.items())
        else: # primitive type
            json_ = object_
    elif isinstance(object_, types.Pandoc):
        version = _configuration["pandoc_types_version"]
        metadata = object_[0]
        blocks = object_[1]
        json_ = odict()
        json_["pandoc-api-version"] = version
        json_["meta"] = write2(object_[0][0])
        json_["blocks"] = write2(object_[1])
    else:
        constructor = type(object_)._def
        data_type = type(object_).__mro__[2]._def
        single_type_constructor = (len(data_type[1][1]) == 1)
        single_constructor_argument = (len(constructor[1][1]) == 1)
        is_record = (constructor[1][0] == "map")

        json_ = odict()
        if not single_type_constructor:
            json_["t"] = type(object_).__name__

        if not is_record:
            c = [write2(arg) for arg in object_]
            if single_constructor_argument:
                c = c[0]
            if single_type_constructor:
                json_ = c
            else:
                if len(c) != []:
                    json_["c"] = c
        else:
            keys = [kt[0] for kt in constructor[1][1]]
            for key, arg in zip(keys, object_):
                json_[key] = write2(arg)
    return json_
    

# Iteration
# ------------------------------------------------------------------------------
def iter(elt, enter=None, exit=None):
    if enter is not None:
        enter(elt)
    yield elt
    if isinstance(elt, dict):
        elt = elt.items()
    if hasattr(elt, "__iter__") and not isinstance(elt, types.String):
        for child in elt:
             for subelt in iter(child, enter, exit):
                 yield subelt
    if exit is not None:
        exit(elt)

def iter_path(elt):
    path = []
    def enter(elt_):
        path.append(elt_)
    def exit(elt_):
        path.pop()
    for elt_ in iter(elt, enter, exit):
        yield path

def get_parent(doc, elt):
    for path in iter_path(doc):
        elt_ = path[-1]
        if elt is elt_:
             parent = path[-2] if len(path) >= 2 else None
             return parent


# Main Entry Point
# ------------------------------------------------------------------------------
def main():
    prog = "python -m pandoc"
    description = "Read/write pandoc JSON documents with Python"
    parser = argparse.ArgumentParser(prog=prog, description=description)

    try:
        stdin = sys.stdin.buffer
    except:
        stdin = sys.stdin
    parser.add_argument("input", 
                        nargs="?", metavar="INPUT",
                        type=argparse.FileType("rb"), default=stdin,
                        help="input file")
    try:
        stdout = sys.stdout.buffer
    except:
        stdout = sys.stdout
    parser.add_argument("-o", "--output", 
                        nargs="?", 
                        type=argparse.FileType("wb"), default=sys.stdout,
                        help="output file")
    args = parser.parse_args()

    input_text = args.input.read()
    if "b" in args.input.mode:
        # given the choice, we interpret the input as utf-8
        input_text = input_text.decode("utf-8")

    try: # try JSON content first
        json_ = json.loads(input_text, object_pairs_hook=collections.OrderedDict)
        doc = read(json_)
    except:
        pass # maybe it's a Python document?
    else:
        doc_repr = (repr(doc) + "\n") # this repr is 7-bit safe.
        if "b" in args.output.mode:
            # given the choice, we use utf-8.
            doc_repr = doc_repr.encode("utf-8")
        args.output.write(doc_repr)
        return
        
    globs = types.__dict__.copy()
    try:
        doc = eval(input_text, globs)
        json_ = write(doc)
    except:
        pass # not a Python document either ...
    else:
        json_repr = (json.dumps(json_) + "\n") # also 7-bit safe
        if "b" in args.output.mode:
            # given the choice, we use utf-8.
            json_repr = json_repr.encode("utf-8")
        args.output.write(json_repr)
        return

    sys.exit("pandoc (python): invalid input document")


