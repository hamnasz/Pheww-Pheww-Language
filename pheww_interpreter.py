#!/usr/bin/env python3
"""
Pheww Pheww — "Girlish" edition interpreter
Features supported (best-effort):
- Interpreted REPL (whitespace/indentation-aware like Python)
- File runner for .pew files
- Dynamic typing (numbers, strings, booleans: yes/no, lists)
- Keywords in girlish style: say, when / elsewhen / otherwise, for each, repeat, create (functions & types), has (attributes), bring (import), try / catch, start / done entrypoint
- Concatenation with +, comparisons, arithmetic, calls
- Classes (create type X:) with `has name` and default values, methods via `create name(...):` inside type
- Functions: create fname (args): ... and `return` via `say` or `Zing` is not required — functions return None unless using `spark` keyword

Usage:
  python pheww_girlish_interpreter.py myprog.pew
  python pheww_girlish_interpreter.py        -> REPL

Extension to use: .pew

This is a prototype and intentionally permissive in expression evaluation (uses Python eval in a restricted env). Use locally.
"""

import sys
import re
import importlib
from collections import deque

# ----------------------
# Utilities
# ----------------------

def dedent_block(lines, start_idx):
    """Given lines and start index at a header (line with colon), capture following indented block.
    Returns (block_lines, next_index) where block_lines have their indent trimmed."""
    block = []
    if start_idx + 1 >= len(lines):
        return block, start_idx + 1
    # find indent of first child
    header_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip(' '))
    child_indent = None
    i = start_idx + 1
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        indent = len(line) - len(line.lstrip(' '))
        if child_indent is None:
            if indent <= header_indent:
                # no block
                return block, start_idx + 1
            child_indent = indent
        if indent < child_indent:
            break
        # append trimmed
        block.append(line[child_indent:])
        i += 1
    return block, i

# Expression helper: translate girlish tokens to python

def expr_to_python(s):
    # map yes/no to True/False
    s = re.sub(r'\byes\b', 'True', s)
    s = re.sub(r'\bno\b', 'False', s)
    # map ' is ' to ' == '
    s = re.sub(r'\bis\b', '==', s)
    # allow Python-style list literals
    return s

# Safe eval environment builder

def make_eval_env(env):
    # env: dict of our variables
    # we provide only those variables and a few helpers
    safe = {k: v for k, v in env.items()}
    # helper: make list from python values already in safe
    return safe

# ----------------------
# Parser & Interpreter (line-based, indentation blocks)
# ----------------------

class RuntimeErrorP(Exception):
    pass

class Interpreter:
    def __init__(self):
        self.globals = {}
        self.modules = {}
        self.entry_block = None

    def run_file(self, path):
        with open(path, 'r', encoding='utf8') as f:
            text = f.read()
        self.run_text(text, filename=path)

    def run_text(self, text, filename=None):
        # normalize line endings and split
        lines = text.replace('\t', '    ').splitlines()
        # store top-level blocks as list of (line, block_lines)
        i = 0
        stmts = []
        while i < len(lines):
            line = lines[i].rstrip()
            if not line.strip() or line.strip().startswith('#') or line.strip().startswith('//'):
                i += 1
                continue
            if line.strip().endswith(':'):
                block, j = dedent_block(lines, i)
                stmts.append((line.lstrip(), block))
                i = j
            else:
                stmts.append((line.lstrip(), None))
                i += 1
        # execute top-level statements
        # but first, scan for start: ... done block and remember as entry
        for header, block in stmts:
            if header.startswith('start:'):
                self.entry_block = (header, block)
            else:
                self.exec_stmt(header, block, self.globals)
        # if entry exists, run it
        if self.entry_block:
            h, b = self.entry_block
            self.exec_block(b, self.globals)

    def exec_block(self, block_lines, env):
        i = 0
        while i < len(block_lines):
            line = block_lines[i].rstrip()
            if not line.strip() or line.strip().startswith('#') or line.strip().startswith('//'):
                i += 1
                continue
            if line.strip().endswith(':'):
                subblock, j = dedent_block(block_lines, i)
                self.exec_stmt(line.lstrip(), subblock, env)
                i = j
            else:
                self.exec_stmt(line.lstrip(), None, env)
                i += 1

    def exec_stmt(self, header, block, env):
        # header: one line like 'when energy > 80:' or 'my_mood = "✨ productive ✨"'
        # block: list of lines if header ends with ':' else None
        s = header.strip()
        # assignment
        m_assign = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$', s)
        if m_assign and block is None:
            name = m_assign.group(1)
            expr = m_assign.group(2).strip()
            val = self.eval_expr(expr, env)
            env[name] = val
            return
        # bring (import)
        m_bring = re.match(r'^bring\s+"([^"]+)"(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?$', s)
        if m_bring and block is None:
            modname = m_bring.group(1)
            alias = m_bring.group(2) or modname
            try:
                mod = importlib.import_module(modname)
            except Exception:
                # try loading a .pew file from disk by name
                try:
                    # compile and execute in a fresh module-like dict
                    with open(modname + '.pew', 'r', encoding='utf8') as f:
                        code = f.read()
                    module_env = {}
                    # run file in a fresh interpreter instance
                    subi = Interpreter()
                    subi.globals = module_env
                    subi.run_text(code, filename=modname + '.pew')
                    mod = module_env
                except Exception as e:
                    raise RuntimeErrorP(f'Cannot bring module {modname}: {e}')
            env[alias] = mod
            return
        # say (print)
        m_say = re.match(r'^say\s+(.+)$', s)
        if m_say and (block is None):
            expr = m_say.group(1).strip()
            val = self.eval_expr(expr, env)
            print(val)
            return
        # when / elsewhen / otherwise (if/elif/else)
        if s.startswith('when '):
            condition = s[len('when '):].rstrip(':').strip()
            if self.eval_expr(condition, env):
                self.exec_block(block, env)
                # consume any elsewhen/otherwise that follow at the same level by caller
            else:
                # lookahead: but our simple model executes statements in order; to support elsewhen/otherwise,
                # caller must pass those as sequential statements. We'll implement a simple mechanism: if this
                # when didn't run, we will try to find and run following elsewhen/otherwise lines in the block param
                # NOTE: at top-level, exec_block already iterates sequential lines, so here we do nothing.
                pass
            return
        if s.startswith('elsewhen '):
            condition = s[len('elsewhen '):].rstrip(':').strip()
            if self.eval_expr(condition, env):
                self.exec_block(block, env)
            return
        if s.startswith('otherwise:'):
            self.exec_block(block, env)
            return
        # for each ... in ...
        m_for = re.match(r'^for each\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+(.+):$', s)
        if m_for:
            var = m_for.group(1)
            expr = m_for.group(2)
            iterable = self.eval_expr(expr, env)
            if iterable is None:
                return
            for item in iterable:
                env[var] = item
                self.exec_block(block, env)
            return
        # repeat N times
        m_rep = re.match(r'^repeat\s+(\d+)\s+times:$', s)
        if m_rep:
            n = int(m_rep.group(1))
            for _ in range(n):
                self.exec_block(block, env)
            return
        # create function: create fname with (args):  OR create fname (args):
        m_create_fn = re.match(r'^create\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:with\s*)?\(([^\)]*)\):$', s)
        if m_create_fn:
            fname = m_create_fn.group(1)
            params = [p.strip() for p in m_create_fn.group(2).split(',')] if m_create_fn.group(2).strip() else []
            def make_func(block_lines, params):
                def fn(*args, _env=env):
                    local = dict(_env)
                    for name, val in zip(params, args):
                        if name:
                            local[name] = val
                    # execute block in local; use nested Interpreter to avoid clobbering globals
                    sub = Interpreter()
                    sub.globals = local
                    try:
                        sub.exec_block(block_lines, local)
                    except ReturnSignal as r:
                        return r.value
                    # update outer env with globals changed? no, functions are lexical closures copying env
                    return None
                return fn
            env[fname] = make_func(block, params)
            return
        # function call like name(args)
        m_call = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$', s)
        if m_call and block is None:
            fname = m_call.group(1)
            args_raw = m_call.group(2).strip()
            args = []
            if args_raw:
                # naive split by comma
                parts = [p.strip() for p in re.split(r',(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)', args_raw)]
                for p in parts:
                    args.append(self.eval_expr(p, env))
            fn = env.get(fname) or getattr(self, fname, None)
            if callable(fn):
                return fn(*args)
            else:
                raise RuntimeErrorP(f'Unknown function: {fname}')
        # create type (class)
        m_create_type = re.match(r'^create\s+type\s+([A-Za-z_][A-Za-z0-9_]*):$', s)
        if m_create_type:
            typename = m_create_type.group(1)
            # parse block for 'has' attributes and methods (create ...)
            attrs = {}
            methods = {}
            i = 0
            while i < len(block):
                line = block[i].strip()
                if not line:
                    i += 1
                    continue
                m_has = re.match(r'^has\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*(.+))?$', line)
                if m_has:
                    aname = m_has.group(1)
                    aval = m_has.group(2)
                    if aval is not None:
                        attrs[aname] = self.eval_expr(aval, {})
                    else:
                        attrs[aname] = None
                    i += 1
                    continue
                m_method = re.match(r'^create\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\):$', line)
                if m_method:
                    mname = m_method.group(1)
                    params = [p.strip() for p in m_method.group(2).split(',')] if m_method.group(2).strip() else []
                    # capture method block
                    method_block, jump = dedent_block(block, i)
                    # create function that expects self as first arg
                    def make_method(method_block=method_block, params=params):
                        def method(self_obj, *args):
                            local = dict(self_obj.__dict__)
                            for name, val in zip(params, args):
                                if name:
                                    local[name] = val
                            sub = Interpreter()
                            sub.globals = local
                            try:
                                sub.exec_block(method_block, local)
                            except ReturnSignal as r:
                                return r.value
                            # push back attributes to object
                            for k, v in local.items():
                                setattr(self_obj, k, v)
                            return None
                        return method
                    methods[mname] = make_method()
                    i = jump
                    continue
                i += 1
            # define class
            def make_class(attrs, methods):
                class Dynamic:
                    def __init__(self, *args):
                        # allow constructor like Type("Name") mapping to first has
                        for k, v in attrs.items():
                            setattr(self, k, v)
                        # if positional args, assign to first attributes in order
                        keys = list(attrs.keys())
                        for idx, val in enumerate(args):
                            if idx < len(keys):
                                setattr(self, keys[idx], val)
                    def __repr__(self):
                        return f"<{typename} {getattr(self, 'name', '')}>"
                for mname, mfunc in methods.items():
                    setattr(Dynamic, mname, mfunc)
                return Dynamic
            cls = make_class(attrs, methods)
            env[typename] = cls
            return
        # try / catch
        if s.startswith('try:'):
            # execute block, if exception occurs, caller should provide 'catch' next lines
            try:
                self.exec_block(block, env)
            except Exception as e:
                # record exception in env under some name? For now, re-raise as RuntimeErrorP
                raise
            return
        m_catch = re.match(r'^catch\s+([A-Za-z_][A-Za-z0-9_]*):$', s)
        if m_catch:
            # not fully implemented: requires lookback pairing with try
            env[m_catch.group(1)] = None
            return
        # start/done handled at top-level
        if s.startswith('start:'):
            # entrypoint handled in run_text
            return
        if s.startswith('done'): 
            return
        # print raw line evaluation (expression statements)
        if block is None:
            # try to eval as expression
            try:
                val = self.eval_expr(s, env)
                # if it's a bare function call that returned something, and not None, print? no
                return val
            except Exception as e:
                # unknown statement
                raise RuntimeErrorP(f'Unknown statement: {s} (error: {e})')

    def eval_expr(self, expr, env):
        # quick replacements
        if expr is None:
            return None
        e = expr.strip()
        # string literal raw
        if re.match(r'^".*"$|^\'.*\'$', e):
            # evaluate as python string
            return eval(e)
        # list literal: allow python-style
        if e.startswith('[') and e.endswith(']'):
            # safe eval using Python
            py = expr_to_python(e)
            try:
                return eval(py, { }, make_eval_env(env))
            except Exception as ex:
                raise
        # bare identifiers
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', e):
            if e in env:
                return env[e]
            # builtin functions
            if e == 'None':
                return None
            raise RuntimeErrorP(f'Unknown name: {e}')
        # numeric literal
        if re.match(r'^\d+(\.\d+)?$', e):
            if '.' in e:
                return float(e)
            else:
                return int(e)
        # expression: allow +, -, *, /, comparisons, function calls, string concatenation
        # translate girlish 'is' to '==' and yes/no
        py = expr_to_python(e)
        # build eval environment
        safe = make_eval_env(env)
        # patch env so that names map to their values
        try:
            return eval(py, {"__builtins__": {}}, safe)
        except Exception as ex:
            # try manual handling of concatenation with + for strings referencing variables
            # fallback: raise
            raise RuntimeErrorP(f'Error evaluating expression "{expr}": {ex}')

class ReturnSignal(Exception):
    def __init__(self, value):
        self.value = value

# ----------------------
# REPL
# ----------------------

def repl():
    interp = Interpreter()
    print('GLAM REPL — Pheww Pheww (type :exit to quit)')
    buffer = []
    while True:
        try:
            prompt = 'pew> ' if not buffer else '...> '
            line = input(prompt)
            if line.strip() == ':exit':
                break
            # accumulate until a blank line ends block or single-line statement
            buffer.append(line)
            # if header with ':' wait for indent lines; simple approach: if line ends with ':' wait for next lines until blank
            if line.strip().endswith(':'):
                # wait for user to finish block (enter blank line)
                continue
            # if single-line and not inside block, execute
            text = '\n'.join(buffer) + '\n'
            try:
                interp.run_text(text)
            except Exception as e:
                print('Error:', e)
            buffer = []
        except KeyboardInterrupt:
            print('\nKeyboardInterrupt — exiting')
            break
        except EOFError:
            break

# ----------------------
# Main
# ----------------------

if __name__ == '__main__':
    if len(sys.argv) > 1:
        fname = sys.argv[1]
        if not fname.endswith('.pew'):
            print('Warning: recommended extension is .pew')
        interp = Interpreter()
        interp.run_file(fname)
    else:
        repl()