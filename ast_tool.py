from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass
import clang.cindex as clang
import os
from pathlib import Path
import json
from unidiff import PatchSet
import re

@dataclass
class CodeRelation:
    """代码关系数据类"""
    source: str
    target: str
    relation_type: str
    file_path: str
    call_type: str  # 'direct', 'virtual', 'function_ptr'
    location: Optional[str] = None  # 调用位置
    ptr_name: Optional[str] = None  # 函数指针名称

@dataclass
class DiffFunction:
    """存储 diff 中函数的信息"""
    name: str
    file_path: str
    start_line: int
    end_line: int
    is_modified: bool  # True if function is modified, False if it contains modified lines

class CodeAnalyzer:
    """代码分析工具类"""
    def __init__(self, project_dir: str, repo_name: str, config: Dict[str, Any] = None):
        """
        初始化代码分析器
        
        Args:
            project_dir: 项目根目录
            repo_name: 仓库名称
            config: 配置字典，包含编译器配置和项目特定配置
        """
        self.project_dir = project_dir
        self.repo_name = repo_name
        
        # 获取配置，如果没有提供则使用默认值
        self.config = config or {}
        
        # 配置 clang
        clang_lib_path = self.config.get('clang_lib_path', 'C:/Program Files/LLVM/bin/libclang.dll')
        self._configure_clang(library_path=clang_lib_path)
        self.index = clang.Index.create()
        
        # 基础编译参数
        self.compile_args = [
            '-x', self.config.get('language', 'c'),  # 语言模式
            f'-std={self.config.get("std", "c11")}',  # 语言标准
        ]
        
        # 添加系统头文件路径
        system_includes = self.config.get('system_includes', [
            '/usr/include',
            '/usr/local/include'
        ])
        self.compile_args.extend([f'-I{path}' for path in system_includes])
        
        # 添加项目头文件路径
        project_includes = self.config.get('project_includes', [])
        if not project_includes:
            # 如果没有指定，则自动扫描项目目录下的所有可能的 include 目录
            project_includes = self._scan_include_dirs(project_dir)
        
        self.compile_args.extend([f'-I{path}' for path in project_includes])
        
        # 添加宏定义
        macros = self.config.get('macros', {})
        for name, value in macros.items():
            if value is None:
                self.compile_args.append(f'-D{name}')
            else:
                self.compile_args.append(f'-D{name}={value}')
        
        # 初始化其他成员
        self.virtual_methods = {}
        self.function_ptrs = {}
        self.function_definitions = {}
        self.function_declarations = {}
        self.modified_functions = {}
        self.affected_functions = set()
        
        # 添加日志文件路径
        self.log_file = os.path.join(project_dir, 'ast_analysis.log')
        # 清空或创建日志文件
        with open(self.log_file, 'w', encoding='utf-8') as f:
            f.write(f"AST Analysis Log for {repo_name}\n{'='*50}\n")

    def _scan_include_dirs(self, root_dir: str) -> List[str]:
        """
        扫描项目目录，查找可能的头文件目录
        """
        include_dirs = set()
        
        for root, dirs, files in os.walk(root_dir):
            # 检查是否包含头文件
            if any(f.endswith(('.h', '.hpp')) for f in files):
                include_dirs.add(root)
            
            # 如果目录名包含这些关键字，很可能是头文件目录
            for dir_name in dirs:
                if any(keyword in dir_name.lower() for keyword in ['include', 'inc', 'header']):
                    include_dirs.add(os.path.join(root, dir_name))
        
        return list(include_dirs)

    def _configure_clang(self, library_path: str):
        if not clang.Config.loaded:
            clang.Config.set_library_file(library_path)

    def _get_function_location(self, cursor: clang.Cursor) -> str:
        """获取函数的位置信息，使用相对路径"""
        if cursor.location.file:
            # 处理 ../../ 这样的相对路径
            file_path = cursor.location.file.name
            
            # 如果路径以 ../ 开头，尝试解析为项目内的路径
            if file_path.startswith('..'):
                try:
                    # 获取文件名
                    file_name = os.path.basename(file_path)
                    # 在项目目录下查找此文件
                    for root, _, files in os.walk(self.project_dir):
                        if file_name in files:
                            rel_path = os.path.relpath(os.path.join(root, file_name), self.project_dir)
                            rel_path = rel_path.replace('\\', '/')
                            # 移除 codebase/nand_analyse 前缀
                            prefix = 'codebase/nand_analyse/'
                            if rel_path.startswith(prefix):
                                rel_path = rel_path[len(prefix):]
                            return f"{rel_path}:{cursor.location.line}:{cursor.location.column}"
                except Exception as e:
                    print(f"Error resolving relative path {file_path}: {str(e)}")
            
            # 如果不是相对路径或找不到文件，使用标准的相对路径处理
            rel_path = self._get_relative_path(file_path)
            return f"{rel_path}:{cursor.location.line}:{cursor.location.column}"
        return ""

    def analyze_file(self, filepath: str) -> List[Dict[str, Any]]:
        try:
            # 添加文件所在目录到include路径
            file_dir = os.path.dirname(filepath)
            current_args = self.compile_args + [f'-I{file_dir}']
            
            # 打印调试信息
            print(f"\nAnalyzing file: {filepath}")
            print("Using compilation arguments:", current_args)
            
            tu = self.index.parse(filepath, args=current_args)
            if not tu:
                print(f"Error parsing {filepath}")
                return []

            # 检查并打印所有诊断信息
            for diag in tu.diagnostics:
                print(f"Diagnostic: {diag.severity} - {diag.spelling}")
                print(f"Location: {diag.location}")

            # 先收集所有函数声明和定义
            for cursor in tu.cursor.walk_preorder():
                if cursor.kind == clang.CursorKind.FUNCTION_DECL:
                    func_name = cursor.spelling
                    if cursor.is_definition():
                        # 存储函数定义
                        self.function_definitions[func_name] = cursor
                    else:
                        # 存储函数声明
                        if func_name not in self.function_declarations:
                            self.function_declarations[func_name] = cursor

            # 然后分析函数关系
            relations = []
            for func_name, cursor in self.function_definitions.items():
                function_relations = self._analyze_function(cursor, filepath)
                relations.extend(function_relations)
                        
            return relations
            
        except Exception as e:
            print(f"Error analyzing file {filepath}: {str(e)}")
            import traceback
            traceback.print_exc()
            return []

    def _is_standard_function(self, func_name: str, file_path: str = None) -> bool:
        """检查是否是标准库函数
        
        Args:
            func_name: 函数名
            file_path: 函数所在文件路径
            
        Returns:
            bool: 如果是标准库函数返回 True
        """
        # 常见的标准库函数
        standard_funcs = {
            'memcpy', 'memset', 'malloc', 'free', 'printf', 'sprintf', 'fprintf',
            'strcpy', 'strncpy', 'strcmp', 'strncmp', 'strlen', 'strcat', 'strncat',
            'fopen', 'fclose', 'fread', 'fwrite', 'fseek', 'ftell',
            'calloc', 'realloc', 'abort', 'exit',
            'time', 'clock', 'rand', 'srand'
        }
        
        # 检查函数名
        if func_name in standard_funcs:
            return True
        
        # 检查文件路径
        if file_path:
            standard_paths = [
                'visual_studio', 'VC', 'gcc', 'include', 'stdlib.h', 'stdio.h',
                'string.h', 'memory.h', 'time.h', 'math.h'
            ]
            return any(path in file_path.lower() for path in standard_paths)
        
        return False

    def _analyze_function(self, cursor: clang.Cursor, filepath: str) -> List[Dict[str, Any]]:
        """分析函数的调用关系"""
        relations = []
        function_name = cursor.spelling
        
        def _process_call(node: clang.Cursor) -> None:
            if node.kind == clang.CursorKind.CALL_EXPR:
                called = node.referenced
                if called and called.location.file:
                    called_name = called.spelling
                    called_file = called.location.file.name
                    
                    # 过滤掉标准库函数
                    if (called_name and 
                        not called_name.startswith(('__', '_')) and 
                        not self._is_standard_function(called_name, called_file) and
                        self._is_project_source_file(called_file)):
                        
                        rel_path = self._get_relative_path(called_file)
                        call_location = self._get_function_location(node)
                        
                        relations.append({
                            'source': function_name,
                            'target': called_name,
                            'file_path': rel_path,
                            'call_type': 'direct',
                            'location': call_location
                        })
                        print(f"Found call: {function_name} -> {called_name} in {rel_path}")

        for child in cursor.walk_preorder():
            _process_call(child)

        return relations

    def _get_relative_path(self, file_path: str) -> str:
        """获取相对于项目目录的路径，并统一使用正斜杠
        
        Args:
            file_path: 完整文件路径
            
        Returns:
            str: 标准化的相对路径
        """
        if not file_path:
            return ""
        
        try:
            # 标准化路径
            abs_path = os.path.abspath(file_path)
            project_path = os.path.abspath(self.project_dir)
            
            # 获取相对路径
            try:
                rel_path = os.path.relpath(abs_path, project_path)
            except ValueError:
                # 如果文件不在项目目录下，尝试查找同名文件
                file_name = os.path.basename(file_path)
                for root, _, files in os.walk(self.project_dir):
                    if file_name in files:
                        rel_path = os.path.relpath(os.path.join(root, file_name), project_path)
                        break
                else:
                    return file_path
            
            # 统一使用正斜杠，移除 codebase/nand_analyse 前缀
            rel_path = rel_path.replace('\\', '/')
            prefix = 'codebase/nand_analyse/'
            if rel_path.startswith(prefix):
                rel_path = rel_path[len(prefix):]
            
            return rel_path
            
        except Exception as e:
            print(f"Error normalizing path {file_path}: {str(e)}")
            return file_path

    def _normalize_path(self, file_path: str) -> str:
        """标准化文件路径，确保能找到正确的文件"""
        if not file_path:
            return ""
        
        # 如果是相对路径，转换为项目目录下的完整路径
        if not os.path.isabs(file_path):
            file_path = os.path.join(self.project_dir, file_path)
        
        # 标准化路径分隔符
        file_path = os.path.normpath(file_path)
        
        # 如果文件不存在，尝试在项目目录下查找
        if not os.path.exists(file_path):
            # 获取文件名
            file_name = os.path.basename(file_path)
            # 在项目目录下递归查找
            for root, _, files in os.walk(self.project_dir):
                if file_name in files:
                    return os.path.join(root, file_name)
        
        return file_path

    def _get_function_code(self, cursor: clang.Cursor) -> str:
        """获取函数的完整代码"""
        try:
            extent = cursor.extent
            if not extent.start.file or not extent.end.file:
                print(f"No file information for function: {cursor.spelling}")
                return ""

            # 获取文件的绝对路径
            file_path = self._normalize_path(extent.start.file.name)
            if not os.path.exists(file_path):
                # 如果找不到文件，尝试在项目目录下查找
                base_name = os.path.basename(file_path)
                for root, _, files in os.walk(self.project_dir):
                    if base_name in files:
                        file_path = os.path.join(root, base_name)
                        break

            if not os.path.exists(file_path):
                print(f"File not found: {file_path}")
                return ""

            print(f"Reading code from file: {file_path}")
            print(f"Function {cursor.spelling} range: {extent.start.line}-{extent.end.line}")

            # 尝试不同的编码方式读取文件
            encodings = ['utf-8', 'gbk', 'gb2312', 'iso-8859-1']
            
            for encoding in encodings:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        lines = f.readlines()
                        
                        # 确保行号在有效范围内
                        if extent.start.line <= 0:
                            print(f"Invalid start line: {extent.start.line}")
                            continue
                        if extent.end.line > len(lines):
                            print(f"Invalid end line: {extent.end.line}, file has {len(lines)} lines")
                            continue
                        
                        # 获取函数的代码行（包括开始行和结束行）
                        code_lines = lines[extent.start.line - 1:extent.end.line]
                        
                        # 不处理列偏移，获取完整的行
                        code = ''.join(code_lines)
                        
                        if code.strip():  # 如果获取到了非空代码
                            print(f"Successfully read {len(code_lines)} lines of code")
                            return code
                        else:
                            print("Got empty code")
                        
                except UnicodeDecodeError:
                    print(f"Failed to decode with {encoding}")
                    continue
                except Exception as e:
                    print(f"Error reading file with {encoding}: {str(e)}")
                    continue
            
            # 如果所有编码都失败，尝试二进制读取
            try:
                with open(file_path, 'rb') as f:
                    content = f.read()
                    # 尝试用不同的编码解码
                    for encoding in encodings:
                        try:
                            text = content.decode(encoding)
                            lines = text.splitlines(True)
                            if extent.start.line <= 0 or extent.end.line > len(lines):
                                continue
                            code = ''.join(lines[extent.start.line - 1:extent.end.line])
                            if code.strip():
                                print(f"Successfully read code in binary mode with {encoding}")
                                return code
                        except:
                            continue
            except Exception as e:
                print(f"Error reading file in binary mode: {str(e)}")

            print(f"Failed to get code for function: {cursor.spelling}")
            return ""
            
        except Exception as e:
            print(f"Error getting function code for {cursor.spelling}: {str(e)}")
            import traceback
            traceback.print_exc()
            return ""

    def _get_function_info(self, func_name: str) -> Dict:
        """获取函数的完整信息，包括声明和定义"""
        result = {
            'name': func_name,
            'repo_name': self.repo_name,
            'is_definition': False
        }
        
        # 首先查找函数定义
        if func_name in self.function_definitions:
            cursor = self.function_definitions[func_name]
            abs_path = self._normalize_path(cursor.location.file.name if cursor.location.file else None)
            rel_path = self._get_relative_path(abs_path)  # 获取相对路径
            result.update({
                'file_path': rel_path,  # 存储相对路径
                'params': json.dumps([f"{param.type.spelling} {param.spelling}" 
                                   for param in cursor.get_arguments()]),
                'return_type': cursor.result_type.spelling,
                'location': self._get_function_location(cursor),
                'code': self._get_function_code(cursor),
                'is_definition': True
            })
            return result
        
        # 如果没有找到定义，查找声明
        if func_name in self.function_declarations:
            cursor = self.function_declarations[func_name]
            header_file = cursor.location.file.name if cursor.location.file else None
            
            # 尝试从头文件找到对应的源文件
            if header_file and header_file.endswith(('.h', '.hpp')):
                source_file = self._find_source_file(header_file)
                if source_file:
                    # 解析源文件
                    try:
                        tu = self.index.parse(source_file, args=self.compile_args)
                        if tu:
                            # 在源文件中查找函数定义
                            for c in tu.cursor.walk_preorder():
                                if (c.kind == clang.CursorKind.FUNCTION_DECL and 
                                    c.is_definition() and 
                                    c.spelling == func_name):
                                    # 找到函数定义
                                    rel_path = self._get_relative_path(source_file)  # 获取相对路径
                                    result.update({
                                        'file_path': rel_path,  # 存储相对路径
                                        'params': json.dumps([f"{param.type.spelling} {param.spelling}" 
                                                           for param in c.get_arguments()]),
                                        'return_type': c.result_type.spelling,
                                        'location': self._get_function_location(c),
                                        'code': self._get_function_code(c),
                                        'is_definition': True
                                    })
                                    return result
                    except Exception as e:
                        print(f"Error parsing source file {source_file}: {str(e)}")
            
            # 如果没有找到源文件或解析失败，返回声明信息
            rel_path = self._get_relative_path(header_file)  # 获取相对路径
            declaration_code = self._get_function_declaration(cursor)
            result.update({
                'file_path': rel_path,  # 存储相对路径
                'params': json.dumps([f"{param.type.spelling} {param.spelling}" 
                                   for param in cursor.get_arguments()]),
                'return_type': cursor.result_type.spelling,
                'location': self._get_function_location(cursor),
                'code': declaration_code,
                'is_declaration': True
            })
            return result
        
        return result

    def _find_source_file(self, header_file: str) -> Optional[str]:
        """根据头文件路径查找对应的源文件
        
        Args:
            header_file: 头文件的完整路径
            
        Returns:
            Optional[str]: 源文件的完整路径，如果没找到则返回 None
        """
        # 获取头文件的目录和文件名
        dir_path = os.path.dirname(header_file)
        base_name = os.path.splitext(os.path.basename(header_file))[0]
        
        # 可能的源文件扩展名
        source_extensions = ['.c', '.cpp']
        
        # 首先在同一目录下查找
        for ext in source_extensions:
            source_file = os.path.join(dir_path, base_name + ext)
            if os.path.exists(source_file):
                return source_file
        
        # 如果没找到，在常见的源代码目录中查找
        common_source_dirs = ['src', 'source', 'sources', 'impl', 'implementation']
        parent_dir = os.path.dirname(dir_path)
        
        for source_dir in common_source_dirs:
            search_dir = os.path.join(parent_dir, source_dir)
            if os.path.exists(search_dir):
                for ext in source_extensions:
                    source_file = os.path.join(search_dir, base_name + ext)
                    if os.path.exists(source_file):
                        return source_file
        
        # 如果还没找到，递归搜索项目目录
        for root, _, files in os.walk(self.project_dir):
            for file in files:
                if file.startswith(base_name) and any(file.endswith(ext) for ext in source_extensions):
                    return os.path.join(root, file)
        
        return None

    def _get_function_declaration(self, cursor: clang.Cursor) -> str:
        """获取函数声明的代码"""
        try:
            extent = cursor.extent
            if extent.start.file and extent.end.file:
                with open(extent.start.file.name, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    # 获取声明的代码行
                    declaration_lines = lines[extent.start.line - 1:extent.end.line]
                    # 处理第一行和最后一行的列偏移
                    if len(declaration_lines) > 0:
                        declaration_lines[0] = declaration_lines[0][extent.start.column - 1:]
                    if len(declaration_lines) > 1:
                        declaration_lines[-1] = declaration_lines[-1][:extent.end.column - 1]
                    return ''.join(declaration_lines)
            return ""
        except Exception as e:
            print(f"Error getting function declaration for {cursor.spelling}: {str(e)}")
            return ""

    def _find_function_declaration(self, func_name: str) -> Optional[Dict]:
        """在项目中搜索函数声明"""
        try:
            # 搜索所有头文件
            for root, _, files in os.walk(self.project_dir):
                for file in files:
                    if file.endswith(('.h', '.hpp')):
                        header_path = os.path.join(root, file)
                        tu = self.index.parse(header_path, args=self.compile_args)
                        if not tu:
                            continue
                        
                        for cursor in tu.cursor.walk_preorder():
                            if (cursor.kind == clang.CursorKind.FUNCTION_DECL and 
                                cursor.spelling == func_name):
                                return {
                                    'file_path': header_path,
                                    'params': json.dumps([f"{param.type.spelling} {param.spelling}" 
                                                       for param in cursor.get_arguments()]),
                                    'return_type': cursor.result_type.spelling,
                                    'location': self._get_function_location(cursor),
                                    'code': self._get_function_declaration(cursor),
                                    'is_declaration': True
                                }
        except Exception as e:
            print(f"Error searching for function declaration {func_name}: {str(e)}")
        return None

    def parse_diff(self, diff_content: str) -> None:
        """解析 git diff 内容，找出修改的函数"""
        print("Parsing diff content...")
        
        try:
            patch_set = PatchSet(diff_content)
            
            if not patch_set:
                print("Error: PatchSet is empty")
                return
            
            for patched_file in patch_set:
                file_path = patched_file.path
                if not file_path.endswith(('.c', '.cpp', '.h', '.hpp')):
                    continue

                # 获取修改的行号范围
                modified_lines = set()
                for hunk in patched_file:
                    # 获取修改的行号范围
                    start_line = hunk.target_start
                    for i, line in enumerate(hunk):
                        if line.is_added or line.is_removed:
                            modified_lines.add(start_line + i)

                # 分析包含修改行的函数
                self._analyze_file_functions(file_path, modified_lines)

        except Exception as e:
            print(f"Error parsing diff: {str(e)}")
            import traceback
            traceback.print_exc()

    def _analyze_file_functions(self, file_path: str, modified_lines: Set[int]) -> None:
        """分析文件中的函数定义和调用关系
        
        Args:
            file_path: 文件路径
            modified_lines: 修改的行号集合
        """
        try:
            full_path = os.path.join(self.project_dir, file_path)
            
            # 解析文件
            tu = self.index.parse(full_path, args=self.compile_args)
            if not tu:
                return

            # 只收集包含修改行的函数定义
            for cursor in tu.cursor.walk_preorder():
                if (cursor.kind == clang.CursorKind.FUNCTION_DECL and 
                    cursor.is_definition() and 
                    cursor.location and 
                    cursor.location.file):
                    
                    # 检查函数是否在当前文件中
                    cursor_file = os.path.normpath(cursor.location.file.name)
                    current_file = os.path.normpath(full_path)
                    
                    if cursor_file == current_file:
                        start_line = cursor.extent.start.line
                        end_line = cursor.extent.end.line
                        
                        # 检查函数是否包含修改的行
                        if any(start_line <= line <= end_line for line in modified_lines):
                            # 验证是否有实际的代码修改（不是注释或空行）
                            has_real_changes = False
                            try:
                                with open(full_path, 'r', encoding='utf-8') as f:
                                    file_lines = f.readlines()
                                    for line_num in modified_lines:
                                        if start_line <= line_num <= end_line:
                                            if line_num - 1 < len(file_lines):
                                                line_content = file_lines[line_num - 1].strip()
                                                if line_content and not line_content.startswith(('//', '/*', '*', '*/')):
                                                    has_real_changes = True
                                                    break
                            except Exception as e:
                                print(f"Error reading file content: {str(e)}")
                            
                            if has_real_changes:
                                print(f"Found modified function: {cursor.spelling} ({start_line}-{end_line})")
                                self.modified_functions[cursor.spelling] = DiffFunction(
                                    name=cursor.spelling,
                                    file_path=file_path,
                                    start_line=start_line,
                                    end_line=end_line,
                                    is_modified=True
                                )
                                # 同时存储函数定义供后续分析使用
                                self.function_definitions[cursor.spelling] = cursor

        except Exception as e:
            print(f"Error analyzing functions in {file_path}: {str(e)}")

    def _log(self, message: str):
        """写入日志到文件"""
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(f"{message}\n")

    def _is_project_function(self, func_name: str, file_path: str = None) -> bool:
        """检查是否是项目中的函数（非系统函数）
        
        Args:
            func_name: 函数名
            file_path: 函数所在文件路径
            
        Returns:
            bool: 如果是项目中的函数返回 True
        """
        # 如果没有文件路径，直接返回 False
        if not file_path:
            return False
        
        # 标准化路径
        abs_file_path = os.path.abspath(file_path)
        abs_project_dir = os.path.abspath(self.project_dir)
        
        # 排除系统函数和内部函数
        if (func_name.startswith(('__', '_', 'printf', 'scanf', 'malloc', 'free')) or
            any(path in abs_file_path.lower() for path in [
                'include', 'lib', 'libs', 'visual_studio', 'vc', 'gcc'
            ])):
            return False
        
        # 检查文件是否在项目目录下
        is_in_project = abs_file_path.startswith(abs_project_dir)
        
        # 获取相对路径
        if is_in_project:
            rel_path = os.path.relpath(abs_file_path, abs_project_dir)
            # 排除测试文件和第三方库文件
            if any(part in rel_path.lower() for part in ['test', 'mock', 'stub', 'third_party', 'vendor']):
                return False
            
            # 只处理源代码文件
            return rel_path.endswith(('.c', '.cpp', '.h', '.hpp'))
        
        return False

    def _extract_modified_functions_from_diff(self, code_changes: List[Dict[str, str]]) -> Set[str]:
        """从 code_changes 中提取修改的函数名"""
        modified_functions = set()
        try:
            print(f"\nProcessing {len(code_changes)} file changes...")
            
            for change in code_changes:
                file_path = change['path']
                diff_content = change['diff']
                
                print(f"\nAnalyzing file: {file_path}")
                
                if not file_path.endswith(('.c', '.cpp', '.h', '.hpp')):
                    print(f"Skipping non-C/C++ file: {file_path}")
                    continue
                
                # 解析文件找出修改的函数
                full_path = os.path.join(self.project_dir, file_path)
                if not os.path.exists(full_path):
                    print(f"File not found: {full_path}")
                    continue
                
                tu = self.index.parse(full_path, args=self.compile_args)
                if not tu:
                    print(f"Failed to parse file with clang: {file_path}")
                    continue
                
                # 从 diff 内容中提取函数名和修改的行
                modified_lines = set()
                current_line = None
                has_real_changes = False
                function_lines = {}  # 存储每个函数的行范围
                current_function = None
                
                # 首先解析整个文件，获取所有函数的位置信息
                for cursor in tu.cursor.walk_preorder():
                    if (cursor.kind == clang.CursorKind.FUNCTION_DECL and 
                        cursor.is_definition() and 
                        cursor.location and 
                        cursor.location.file and 
                        self._is_project_function(cursor.spelling, cursor.location.file.name)):
                        
                        # 检查函数是否在当前分析的文件中
                        cursor_file_path = os.path.normpath(cursor.location.file.name)
                        current_file_path = os.path.normpath(full_path)
                        
                        if cursor_file_path == current_file_path:
                            function_lines[cursor.spelling] = (cursor.extent.start.line, cursor.extent.end.line)
                
                # 解析 diff 内容
                for line in diff_content.split('\n'):
                    # 处理 diff 头部
                    if line.startswith('@@'):
                        try:
                            header_match = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
                            if header_match:
                                current_line = int(header_match.group(1))
                            continue
                        except Exception as e:
                            print(f"Error parsing diff header: {str(e)}")
                            continue
                    
                    if current_line is not None:
                        # 检查是否是函数定义的修改
                        line_content = line[1:] if line.startswith(('+', '-')) else line
                        
                        # 检查是否是实际的代码改动（不是空行或只有空白字符）
                        if line.startswith(('+', '-')) and line_content.strip():
                            has_real_changes = True
                            if line.startswith('+'):
                                modified_lines.add(current_line)
                            elif line.startswith('-') and current_line > 1:
                                modified_lines.add(current_line - 1)
                        
                        if line.startswith(' '):
                            current_line += 1
                        elif line.startswith('+'):
                            current_line += 1
                
                # 只有在有实际代码改动时才继续处理
                if has_real_changes:
                    print(f"Modified lines in {file_path}: {sorted(modified_lines)}")
                    
                    # 检查每个修改的行是否在某个函数内
                    for func_name, (start_line, end_line) in function_lines.items():
                        if any(start_line <= line <= end_line for line in modified_lines):
                            # 再次验证这个函数是否真的被修改（不是只改了空行）
                            func_modified = False
                            for line in modified_lines:
                                if start_line <= line <= end_line:
                                    # 获取这一行的实际内容
                                    try:
                                        with open(full_path, 'r', encoding='utf-8') as f:
                                            file_lines = f.readlines()
                                            if line - 1 < len(file_lines):
                                                line_content = file_lines[line - 1].strip()
                                                if line_content and not line_content.startswith(('//', '/*', '*', '*/')):
                                                    func_modified = True
                                                    break
                                    except Exception as e:
                                        print(f"Error reading file content: {str(e)}")
                            
                            if func_modified:
                                print(f"Found modified function: {func_name} ({start_line}-{end_line})")
                                modified_functions.add(func_name)
                                self.function_definitions[func_name] = next(
                                    c for c in tu.cursor.walk_preorder()
                                    if c.kind == clang.CursorKind.FUNCTION_DECL and
                                    c.spelling == func_name and
                                    c.is_definition()
                                )
                else:
                    print(f"No real code changes found in {file_path}, skipping function analysis")
            
            print(f"\nTotal modified functions found: {len(modified_functions)}")
            print(f"Modified functions: {modified_functions}")
            return modified_functions
            
        except Exception as e:
            print(f"Error extracting modified functions: {str(e)}")
            import traceback
            traceback.print_exc()
            return set()

    def _is_project_source_file(self, file_path: str) -> bool:
        """检查是否是项目源文件"""
        if not file_path:
            return False
        
        # 标准化路径
        file_path = os.path.normpath(file_path)
        
        try:
            # 检查是否在项目目录下
            rel_path = os.path.relpath(file_path, self.project_dir)
            
            # 排除第三方库目录
            exclude_dirs = ['lib', 'libs']
            if any(part in exclude_dirs for part in rel_path.split(os.sep)):
                return False
            
            # 对于函数定义的检查，只关注 .c/.cpp 文件
            # 对于函数声明的检查，同时考虑 .h/.hpp 文件
            return file_path.endswith(('.c', '.cpp', '.h', '.hpp'))
            
        except ValueError:
            return False

    def _find_all_callers(self, func_name: str) -> List[Dict]:
        """在项目源代码中查找所有调用指定函数的函数"""
        callers = []
        print(f"\nSearching for callers of function: {func_name}")
        
        # 扫描整个项目目录
        for root, _, files in os.walk(self.project_dir):
            # 先处理头文件，收集函数声明
            header_files = [f for f in files if f.endswith(('.h', '.hpp'))]
            for file in header_files:
                file_path = os.path.join(root, file)
                if self._is_project_source_file(file_path):
                    try:
                        tu = self.index.parse(file_path, args=self.compile_args)
                        if tu:
                            for cursor in tu.cursor.walk_preorder():
                                if cursor.kind == clang.CursorKind.FUNCTION_DECL:
                                    self.function_declarations[cursor.spelling] = cursor
                    except Exception as e:
                        print(f"Error parsing header file {file_path}: {str(e)}")
            
            # 然后分析源文件中的函数调用
            source_files = [f for f in files if f.endswith(('.c', '.cpp'))]
            for file in source_files:
                file_path = os.path.join(root, file)
                if not self._is_project_source_file(file_path):
                    continue
                
                rel_path = self._get_relative_path(file_path)
                print(f"Analyzing source file: {rel_path}")
                
                try:
                    tu = self.index.parse(file_path, args=self.compile_args)
                    if not tu:
                        continue
                    
                    # 查找所有函数定义
                    for cursor in tu.cursor.walk_preorder():
                        if (cursor.kind == clang.CursorKind.FUNCTION_DECL and 
                            cursor.is_definition()):
                            
                            # 分析这个函数的调用
                            relations = self._analyze_function(cursor, rel_path)
                            
                            # 检查是否调用了目标函数
                            for relation in relations:
                                if relation['target'] == func_name:
                                    caller_info = self._get_function_info(cursor.spelling)
                                    if caller_info:
                                        callers.append({
                                            "caller": caller_info,
                                            "location": relation['location']
                                        })
            
                except Exception as e:
                    print(f"Error analyzing source file {rel_path}: {str(e)}")
                    continue
        
        return callers

    def analyze_pr_changes(self, changes_data: Dict[str, List[Dict[str, str]]]) -> Dict[str, Any]:
        """分析 PR 变更中的函数调用关系
        
        Args:
            changes_data: 包含代码变更信息的字典
                {
                    "code_changes": [
                        {
                            "path": "file/path",
                            "diff": "@@ ... @@\n..."
                        },
                        ...
                    ]
                }
                
        Returns:
            Dict[str, Any]: 包含修改函数及其调用关系的字典
        """
        # 从 changes_data 中提取 code_changes
        code_changes = changes_data.get('code_changes', [])
        if not code_changes:
            print("No code changes found in input data")
            return {"functions": []}
        
        # 从 code_changes 中提取修改的函数
        modified_functions = self._extract_modified_functions_from_diff(code_changes)
        
        result = {"functions": []}
        
        # 分析每个修改的函数
        for func_name in modified_functions:
            function_info = self._get_function_info(func_name)
            if not function_info:
                continue
            
            print(f"\nAnalyzing function: {func_name}")
            
            # 获取此函数调用的函数
            cursor = self.function_definitions.get(func_name)
            if cursor:
                relations = self._analyze_function(cursor, function_info['file_path'])
                callees = []
                for relation in relations:
                    if self._is_project_function(relation['target']):
                        callee_info = self._get_function_info(relation['target'])
                        if callee_info:
                            callees.append({
                                "callee": callee_info,
                                "location": relation['location']
                            })
            
            # 查找调用此函数的函数
            callers = self._find_all_callers(func_name)
            
            # 添加到结果中
            result["functions"].append({
                "function": function_info,
                "callers": [{"caller": c["caller"], "location": c["location"]} for c in callers],
                "callees": callees
            })

        # 将结果写入文件
        output_file = os.path.join(self.project_dir, 'function_analysis_result.txt')
        with open(output_file, 'w', encoding='utf-8') as f:
            for func_data in result["functions"]:
                f.write(f"\n=== 修改的函数 ===\n")
                func = func_data["function"]
                f.write(f"函数: {func['name']}\n")
                f.write(f"文件: {func.get('file_path', '未知')}\n")
                f.write(f"参数: {func.get('params', '[]')}\n")
                f.write(f"代码:\n{func.get('code', '代码未找到')}\n")
                
                f.write("\n--- 被以下函数调用 ---\n")
                if func_data["callers"]:
                    for call in func_data["callers"]:
                        caller = call["caller"]
                        f.write(f"\n调用者: {caller['name']}\n")
                        f.write(f"文件: {caller.get('file_path', '未知')}\n")
                        f.write(f"调用位置: {call['location']}\n")
                        # 添加调用者的代码
                        f.write(f"调用者代码:\n{caller.get('code', '代码未找到')}\n")
                else:
                    f.write("没有找到调用此函数的函数\n")
                
                f.write("\n--- 调用了以下函数 ---\n")
                if func_data["callees"]:
                    for call in func_data["callees"]:
                        callee = call["callee"]
                        f.write(f"\n被调用函数: {callee['name']}\n")
                        f.write(f"文件: {callee.get('file_path', '未知')}\n")
                        f.write(f"调用位置: {call['location']}\n")
                        # 添加被调用函数的代码
                        f.write(f"被调用函数代码:\n{callee.get('code', '代码未找到')}\n")
                else:
                    f.write("此函数没有调用其他函数\n")
                
                f.write("\n" + "="*80 + "\n")

        print(f"分析结果已写入文件: {output_file}")
        return result

    def get_analysis_as_string(self, result: dict) -> str:
        output = ""
        for func_data in result["functions"]:
            output += f"\n=== 修改的函数 ===\n"
            func = func_data["function"]
            output += f"函数: {func['name']}\n"
            output += f"文件: {func.get('file_path', '未知')}\n"
            output += f"参数: {func.get('params', '[]')}\n"
            output += f"代码:\n{func.get('code', '代码未找到')}\n"
            
            output += "\n--- 被以下函数调用 ---\n"
            if func_data["callers"]:
                for call in func_data["callers"]:
                    caller = call["caller"]
                    if not caller.get("code"):
                        continue

                    output += f"\n调用者: {caller['name']}\n"
                    output += f"文件: {caller.get('file_path', '未知')}\n"
                    output += f"调用位置: {call['location']}\n"
                    # 添加调用者的代码
                    output += f"调用者代码:\n{caller.get('code', '代码未找到')}\n"
            else:
                output += "没有找到调用此函数的函数\n"
            
            output += "\n--- 调用了以下函数 ---\n"
            if func_data["callees"]:
                for call in func_data["callees"]:
                    callee = call["callee"]
                    if not callee.get("code"):
                        continue

                    output += f"\n被调用函数: {callee['name']}\n"
                    output += f"文件: {callee.get('file_path', '未知')}\n"
                    output += f"调用位置: {call['location']}\n"
                    # 添加被调用函数的代码
                    output += f"被调用函数代码:\n{callee.get('code', '代码未找到')}\n"
            else:
                output += "此函数没有调用其他函数\n"
            
            output += "\n" + "="*80 + "\n"
        
        return output

if __name__ == "__main__":
    project_dir = os.path.join(os.getcwd(), "codebase/nand_analyse")

    # 配置示例
    config = {
        'language': 'c',
        'std': 'c11',
        'system_includes': ['/usr/include', '/usr/local/include'],
        'macros': {
            '__CODE_GENERATOR__': None,
            'NAND_TYPE': 'MICRON_B16',
            'ENABLE_SLC_MODE': '1',
            'ASSERT(x,y,z)': '((void)0)'
        }
    }
    
    # 使用新的 JSON 格式
    changes_data = {
        "code_changes": [
            {
                "path": "common/cpu.c",
                "diff": "@@ -55,28 +55,36 @@\n     2,\n     2\n };\n \n \n /************************************************\n  * Function definition\n *************************************************/\n \n /**\n- *  GetCPUID: return CPU ID\n- *    return: 0 - failure; [1h, dh] current cpu id\n- **/\n-U32 GetCPUID()\n+ * @brief 获取CPU ID\n+ * @param[out] pu32CpuId CPU ID输出参数\n+ * @return COMMON_OK: 成功, COMMON_ERR: 失败\n+ */\n+U32 GetCPUID(U32 *pu32CpuId)\n {\n-    U32 u32CpuId = 0;\n-    u32CpuId = GET_REG32(REG_CPUID) & 0x0F;\n-    return u32CpuId;\n+    /* 参数检查 */\n+    if (NULL == pu32CpuId)\n+    {\n+        return COMMON_ERR;\n+    }\n+\n+    /* 读取CPU ID */\n+    *pu32CpuId = GET_REG32(REG_CPU_ID);\n+\n+    return COMMON_OK;\n }\n \n const char* GetCPUName()\n {\n     return g_as8CPUName[GetCPUID()];\n }\n \n void SpinLock(HWLocker_s stHWLock)\n {\n     U32 u32HwArbReq = GET_REG32(REG_HW_ARB_REQ);\n@@ -110,11 +110,10 @@\n \n void SpinLockSharedLog()\n {\n     SpinLock(g_stSharedLogLock);\n }\n \n void SpinUnlockSharedLog()\n {\n     SpinUnlock(g_stSharedLogLock);\n }\n-"
            },
            {
                "path": "ftlp/src/ftlp.h",
                "diff": "@@ -99,100 +99,81 @@\n     U32 u32InValid:     1;\n     U32 u32BmpValid:    1;\n     U32 u32Rsvd1:       2;\n     U32 u32StsCode:     15;\n     U32 u32Rsvd2:       1;\n     U32 u32DefBmp:      8;\n }WrFeature_t;\n \n typedef struct tagHdmaCmd_t\n {\n-    /* DW0 */\n-\tunion\n-\t{\n-\t\tstruct{\n-\t\t\tU32 u32Bp:          24;\n-    \t\tU32 u32PostCmdNo:   8;\n-\t\t}Bits;\n-\t\tU32 Dw;\n-\t}DW0;\n-\n-    /* DW1 */\n-\tunion\n-\t{\n-\t\tstruct{\n-\t\t\tU32 u32CmdType:     2;\n-\t\t\tU32 u32Rsvd1:       6;\n-\t\t\tU32 u32AesBypass:   1;\n-\t\t\tU32 u32AecKEy:      6;\n-\t\t\tU32 u32Rsvd2:       1;\n-\t\t\tU32 u32ForceHrsta:  1;\n-\t\t\tU32 u32Rsvd3:       7;\n-\t\t\tU32 u32Exclusive:   1;\n-\t\t\tU32 u32ArbitLock:   1;\n-\t\t\tU32 u32Rsvd4:       6;\n-\t\t}Bits;\n-\t\tU32  DW;\n-\t}DW1;\n-\n-    /* DW2 */\n-    union\n-    {\n-        RdFeature_t stRdFeature;\n-        WrFeature_t stWrFeature;\n+    /* Command Double Word 0 - Base Parameters */\n+    union {\n+        struct {\n+            U32 u32Bp         : 24;    /* Base Parameter */\n+            U32 u32PostCmdNo  : 8;     /* Post Command Number */\n+        } Bits;\n+        U32 Dw;\n+    } DW0;\n+\n+    /* Command Double Word 1 - Control Parameters */\n+    union {\n+        struct {\n+            U32 u32CmdType    : 2;     /* Command Type */\n+            U32 u32Rsvd1      : 6;     /* Reserved */\n+            U32 u32AesBypass  : 1;     /* AES Bypass */\n+            U32 u32AecKey     : 6;     /* AEC Key */\n+            U32 u32Rsvd2      : 1;     /* Reserved */\n+            U32 u32ForceHrsta : 1;     /* Force HRSTA */\n+            U32 u32Rsvd3      : 7;     /* Reserved */\n+            U32 u32Exclusive  : 1;     /* Exclusive Access */\n+            U32 u32ArbitLock  : 1;     /* Arbitration Lock */\n+            U32 u32Rsvd4      : 6;     /* Reserved */\n+        } Bits;\n         U32 DW;\n-    }uCmdFeature;\n-}HdmaCmd_t;\n+    } DW1;\n \n+    /* Command Double Word 2 - Feature Parameters */\n+    union {\n+        RdFeature_t stRdFeature;    /* Read Feature */\n+        WrFeature_t stWrFeature;    /* Write Feature */\n+        U32         DW;             /* Raw Data */\n+    } uCmdFeature;\n+} HdmaCmd_t;\n \n-typedef union tagHdmaSta_u\n-{\n-\tstruct {\n-\t\tU32 u32PostCmdNo:   8;\n-\t\tU32 u32PcieErr:     1;\n-\t\tU32 u32AbortFlag:   1;\n-\t\tU32 u32DdrEccErr:   1;\n-\t\tU32 u32CrcErr:      1;\n-\t\tU32 u32RDWR:        1;\n-\t\tU32 u32Rsvd:        19;\n-\t}Bits;\n-\tU32 Dw;\n-}HdmaSta_u;\n-#define HDMA_RSP_ERR_STA_MASK       (0xf00)\n-\n-enum\n+typedef union tagHdmaSta_u \n {\n-    NHC_OPC_FLUSH        = 0,\n-    NHC_OPC_WRITE        = 1,\n-    NHC_OPC_READ         = 2,\n-    NHC_OPC_WR_UNC       = 4,\n-    NHC_OPC_CMP          = 5,\n-    NHC_OPC_WR_ZERO      = 8,\n-    NHC_OPC_DSM          = 9,\n-\n-    LPT_DEFAULT        = 0x00000000,\n-    LPT_IN_FLASH       = 0x20000000,\n-    LPT_IN_CACHE       = 0x40000000,\n-    LPT_TRIMMED        = 0x80000000,\n-\n-    CMD_STS_UNSUPPORT    = 0x1,\n-    CMD_STS_TRANS_ERR    = 0x4,\n-    CMD_STS_ABORTED      = 0x7,\n-    CMD_STS_INV_N_SPACE  = 0xb,\n-    CMD_STS_OUT_RANGE    = 0x80,\n-\n-    HDMA_STA_WR          = 0,\n-    HDMA_STA_RD          = 1,\n-\n-    HDMA_FEATURE_RD      = 0,\n-    HDMA_FEATURE_WR      = 1\n-};\n+    struct {\n+        U32 u32PostCmdNo : 8;    /* Post Command Number */\n+        U32 u32PcieErr   : 1;    /* PCIE Error Flag */\n+        U32 u32AbortFlag : 1;    /* Abort Flag */\n+        U32 u32DdrEccErr : 1;    /* DDR ECC Error */\n+        U32 u32CrcErr    : 1;    /* CRC Error */\n+        U32 u32RDWR      : 1;    /* Read/Write Flag */\n+        U32 u32Rsvd      : 19;   /* Reserved */\n+    } Bits;\n+    U32 Dw;\n+} HdmaSta_u;\n+\n+/* HDMA Response Error Status Mask */\n+#define HDMA_RSP_ERR_STA_MASK    (0xf00)\n+\n+/* NHC Operation Codes */\n+typedef enum {\n+    NHC_OPC_FLUSH    = 0,    /* Flush operation */\n+    NHC_OPC_WRITE    = 1,    /* Write operation */\n+    NHC_OPC_READ     = 2,    /* Read operation */\n+    NHC_OPC_WR_UNC   = 4,    /* Write uncached */\n+    NHC_OPC_CMP      = 5,    /* Compare operation */\n+    NHC_OPC_WR_ZERO  = 8     /* Write zeros */\n+} NHC_OPC_E;\n+\n /************************************************\n  * Global variable declaration\n *************************************************/\n \n /************************************************\n  * Function Prototype declaration\n *************************************************/\n \n \n #endif /* __FTLP_H__ */"
            },
            {
                "path": "ftlp/src/main.c",
                "diff": "@@ -52,25 +52,49 @@\n {\n     U32 u32LBA;\n     U32 *pu32LBA = (U32 *)GET_LBA_LPT_ADDR(0);\n     for(u32LBA = 0; u32LBA < LBA_MAX_CNT; ++u32LBA)\n     {\n         *pu32LBA = INVALID_LPT_ENTRY;\n         ++pu32LBA;\n     }\n }\n \n+/**\n+ * @brief 释放写任务\n+ *\n+ * 该函数用于释放写任务。\n+ *\n+ * @param u8PostCmdNo 写任务编号\n+ */\n static void FreeWrJob(U8 u8PostCmdNo)\n {\n-    /* wait not full status */\n-    while(GET_REG32(REG_WRRELEASE_FIFO_STS) & 0x1)\n-        ;\n+    // 设置一个超时时间（单位：毫秒或微秒，根据具体实现）\n+    U32 u32Timeout = 1000; \n+    // 获取当前时间戳\n+    U32 u32StartTick = TIMER_GetTick(); \n+\n+    /* wait not full status with timeout */\n+    // 等待寄存器非满状态，并设置超时\n+    while (GET_REG32(REG_WRRELEASE_FIFO_STS) & 0x1)\n+    {\n+        // 判断是否超时\n+        if (TIMER_Elapsed(u32StartTick) > u32Timeout)\n+        {\n+            // 打印错误信息\n+            PRINT(DBG_ERR, \"FreeWrJob timeout\\r\\n\");\n+            // 超时退出\n+            return; \n+        }\n+    }\n+\n+    // 将命令编号写入寄存器\n     SET_REG32(REG_WRRELEASE_DATA, u8PostCmdNo);\n }\n \n static void SetHDMA(HdmaCmd_t *pstHdmaCmd)\n {\n //    U32 *pu32Data = (U32 *)pstHdmaCmd;\n     /* check not full status */\n     while(GET_REG32(REG_HDMA_CMD_FIFO_STS) & 0x100)\n         ;\n \n@@ -322,21 +322,21 @@\n     HdmaSta_u pstHdmaSta;  // = (HdmaSta_t *)&u32Value;\n     pstHdmaSta.Dw = GET_REG32(REG_HWSTA_HRSTA_FIFO_DATA);\n \n #if DEBUG_HDMA_ERR\n     g_au32ErrValue[g_u32ErrCnt] = u32Value;\n     g_u32ErrCnt++;\n     if(g_u32ErrCnt == 64)\n         g_u32ErrCnt = 0;\n #endif\n     /* have error */\n-//    PRINT(DBG_INFO,\"State u32Value = 0x%x, pst:%d   RW:%d\\r\\n\",pstHdmaSta.Dw, pstHdmaSta.Bits.u32PostCmdNo, pstHdmaSta.Bits.u32RDWR);\n+//    PRINT(DBG_INFO,\"State u32Value = 0x%x, pst:%d   RW:%d\\r\\n\",pstHdmaSta.Dw, pstHdmaSta.Bits.u32PostCmdNo, pstHdmaSta.Bits.u32RDWR);\n     if(pstHdmaSta.Dw & HDMA_RSP_ERR_STA_MASK)\n     {\n         //while(1)\n          //   ;\n         //PRINT(DBG_INFO,\"error u32Value = 0x%x\\r\\n\",u32Value);\n         if(pstHdmaSta.Dw & 0x200) //abort\n         {\n             SetOtherCmdHDMA(pstHdmaSta.Bits.u32PostCmdNo, CMD_STS_ABORTED);\n             //PRINT(DBG_INFO,\"status=%d \\n\",CMD_STS_ABORTED);\n         }else if(pstHdmaSta.Dw & 0x800) //crc error"
            }
        ]
    }
    
    # 创建分析器并分析
    analyzer = CodeAnalyzer(project_dir, "nand_analysis", config)
    results = analyzer.analyze_pr_changes(changes_data)
    
    # 打印结果 - 使用新的格式
    print("\n修改的函数及其调用关系:")
    for func_data in results["functions"]:
        func = func_data["function"]
        print(f"\n=== 函数: {func['name']} ===")
        print(f"文件路径: {func['file_path']}")
        print(f"参数: {func['params']}")
        
        print("\n被以下函数调用:")
        for call in func_data["callers"]:
            caller = call["caller"]
            print(f"- {caller['name']} (在 {call['location']})")
        
        print("\n调用了以下函数:")
        for call in func_data["callees"]:
            callee = call["callee"]
            print(f"- {callee['name']} (在 {call['location']})")
        
        print("-" * 80)
