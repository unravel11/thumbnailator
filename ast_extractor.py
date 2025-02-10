# -*- coding: utf-8 -*-
import datetime
import javalang
import json
import os
import re
from call_graph import CallGraph
import logging
from concurrent.futures import ProcessPoolExecutor

class JavaASTExtractor:
    """Java代码AST分析器，用于分析Java代码的方法调用关系和修改影响。"""

    def __init__(self, logger=None, analyze_stdlib=False):
        """
        初始化AST分析器。
        Args:
            logger: 共享的日志记录器，如果为None则创建新的
            analyze_stdlib: 是否分析标准库函数调用，默认False
        """
        self.ast_data = {}
        self.src_root = None  # 源代码根目录
        self.method_index = {}  # 存储所有方法的索引
        self.call_graph = CallGraph()
        self.analyze_stdlib = analyze_stdlib  # 新增参数
        # 创建输出目录
        self.output_dir = "analysis_results"
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            
        self.logger = logger or self._setup_logger()
        self.ast_cache = {}  # 缓存已解析的AST
        self.class_cache = {}  # 缓存类名解析结果
        self.import_cache = {}  # 缓存导入语句解析结果
        self.field_types = {}  # 缓存字段类型
        self.enum_constants = {}  # 新增：存储枚举常量
        
        # 定义标准库包前缀
        self.stdlib_prefixes = {
            'java.',
            'javax.',
            'sun.',
            'com.sun.',
            'org.w3c.',
            'org.xml.',
            'org.ietf.',
            'org.omg.',
            'org.jcp.',
            'android.',  # 如果需要处理Android项目
        }

        # 定义要排除的包前缀
        self.exclude_prefixes = {
            # Java标准库
            'java.',
            'javax.',
            'sun.',
            'com.sun.',
        }
        
        # 定义要排除的通用方法名
        self.exclude_methods = {
            # 迭代器相关
            'iterator',
            'hasNext',
            'next',
            'remove',
            'forEach',
            'stream',
            'spliterator',
            'listIterator',
            
            # Object类方法
            'toString',
            'equals',
            'hashCode',
            'getClass',
            'clone',
            'notify',
            'notifyAll',
            'wait',
            'finalize',
            
            # 集合类通用方法
            'size',
            'isEmpty',
            'contains',
            'clear',
            'add',
            'remove',
            
            # 其他常见工具方法
            'valueOf',
            'length',
            'trim'
        }

    def _setup_logger(self):
        """配置日志记录器"""
        logger = logging.getLogger('JavaASTExtractor')
        logger.setLevel(logging.DEBUG)
        
        # 创建控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        
        # 创建格式化器
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(formatter)
        
        # 添加处理器到logger
        logger.addHandler(console_handler)
        
        return logger


    def build_project_index(self):
        """扫描整个项目，建立方法索引和调用图"""
        self.logger.info("\n开始扫描项目...")
        
        # 清空所有缓存和索引
        self._clear_caches()
        
        # 获取所有Java文件
        java_files = self._get_java_files()
        self.logger.info(f"找到 {len(java_files)} 个Java文件")
        
        # 使用集合来跟踪已处理的文件
        processed_files = set()
        
        # 第一遍：建立方法索引
        for file_path in java_files:
            if file_path in processed_files or 'package-info.java' in file_path:
                continue
            
            try:
                self.logger.debug(f"\n处理文件: {file_path}")
                self._process_file(file_path)
                processed_files.add(file_path)
            except Exception as e:
                self.logger.error(f"处理文件时出错 {file_path}: {str(e)}")
        
        self.logger.info(f"索引了 {len(self.method_index)} 个方法")
        
        # 第二遍：分析方法调用
        for file_path in processed_files:
            try:
                self._process_file_calls(file_path)
            except Exception as e:
                self.logger.error(f"处理方法调用时出错 {file_path}: {str(e)}")
        
        self.logger.info(f"调用图构建完成，共有 {len(self.call_graph.edges)} 个方法的调用关系")
        
        # 保存调用图
        output_file = os.path.join(self.output_dir, 'call_graph.json')
        self.call_graph.save(output_file)
        self.logger.info(f"调用关系图已保存到: {output_file}")

    def _clear_caches(self):
        """清空所有缓存和索引"""
        self.method_index = {}
        self.ast_cache = {}
        self.class_cache = {}
        self.import_cache = {}
        self.call_graph = CallGraph()
        self.field_types = {}
        self.enum_constants = {}

    def _get_java_files(self):
        """获取所有Java文件的相对路径"""
        java_files = []
        for root, _, files in os.walk(self.src_root):
            for file in files:
                if file.endswith('.java'):
                    # 使用相对路径，并统一使用正斜杠
                    rel_path = os.path.relpath(os.path.join(root, file), self.src_root)
                    rel_path = rel_path.replace('\\', '/')
                    java_files.append(rel_path)
        return java_files

    def _process_file(self, file_path):
        """处理单个文件，提取所有方法信息"""
        try:
            normalized_path = os.path.normpath(file_path)
            full_path = os.path.join(self.src_root, normalized_path)
            
            # 读取并解析文件
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            tree = javalang.parse.parse(content)
            
            # 获取包名和导入信息
            package_name = None
            imports = {}
            
            # 处理包声明
            for _, node in tree.filter(javalang.tree.PackageDeclaration):
                if isinstance(node.name, list):
                    package_name = '.'.join(str(n.value) for n in node.name if hasattr(n, 'value'))
                else:
                    package_name = str(node.name)
                self.logger.debug(f"包名: {package_name}")
                break
            
            # 处理导入语句
            for _, node in tree.filter(javalang.tree.Import):
                try:
                    if node.path:
                        # 获取完整的导入路径
                        if isinstance(node.path, list):
                            import_parts = []
                            for part in node.path:
                                if hasattr(part, 'value'):
                                    import_parts.append(part.value)
                                else:
                                    import_parts.append(str(part))
                            import_path = '.'.join(import_parts)
                        else:
                            import_path = str(node.path)
                        
                        # 获取简单类名（最后一个部分）
                        simple_name = import_path.split('.')[-1]
                        imports[simple_name] = import_path
                        self.logger.debug(f"添加导入: {simple_name} -> {import_path}")
                except Exception as e:
                    self.logger.error(f"处理导入语句时出错: {str(e)}")
                    continue
            
            # 将包名添加到导入信息中
            imports['__package__'] = package_name
            self.import_cache[normalized_path] = imports
            
            # 获取所有字段的类型信息
            field_types = {}
            for _, field_decl in tree.filter(javalang.tree.FieldDeclaration):
                field_type = field_decl.type.name
                # 如果字段类型在导入中有对应的完整类名，使用完整类名
                if field_type in imports:
                    field_type = imports[field_type]
                elif '.' not in field_type and package_name:
                    # 如果是同包的类，添加包名
                    field_type = f"{package_name}.{field_type}"
                
                for var_decl in field_decl.declarators:
                    field_types[var_decl.name] = field_type
                    self.logger.debug(f"添加字段类型: {var_decl.name} -> {field_type}")
            
            # 获取所有局部变量的类型信息
            local_vars = {}
            for _, method_decl in tree.filter(javalang.tree.MethodDeclaration):
                method_local_vars = {}
                
                # 处理方法参数
                if method_decl.parameters:
                    for param in method_decl.parameters:
                        param_type = param.type.name
                        if param_type in imports:
                            param_type = imports[param_type]
                        method_local_vars[param.name] = param_type
                        
                # 处理局部变量声明
                for _, var_decl in method_decl.filter(javalang.tree.LocalVariableDeclaration):
                    var_type = var_decl.type.name
                    if var_type in imports:
                        var_type = imports[var_type]
                    for declarator in var_decl.declarators:
                        method_local_vars[declarator.name] = var_type
                        
                local_vars[method_decl.name] = method_local_vars
            
            # 处理所有类型声明
            for path, type_decl in tree.filter(javalang.tree.ClassDeclaration):
                type_name = type_decl.name
                qualified_name = f"{package_name}.{type_name}"
                
                # 添加类型信息到索引
                type_info = {
                    'file_path': normalized_path,
                    'package': package_name,
                    'name': type_name,
                    'type': 'class',
                    'methods': {},
                    'imports': imports  # 保存导入信息
                }
                self.method_index[qualified_name] = type_info
                
                self.logger.debug(f"添加类型到索引: {qualified_name}")
                self.logger.debug(f"类型信息: {type_info}")
                
                # 处理构造函数
                for constructor in type_decl.constructors:
                    # 构建构造函数名（包含参数类型）
                    params = []
                    if constructor.parameters:
                        params = [self._get_type_name(param.type) for param in constructor.parameters]
                    constructor_name = f"{qualified_name}.{type_name}"
                    if params:
                        constructor_name += '#' + '#'.join(params)
                    
                    # 获取构造函数的位置信息
                    start_line = constructor.position.line if hasattr(constructor, 'position') and constructor.position else None
                    end_line = self._find_node_end_line(constructor)
                    
                    self.logger.debug(f"构造函数位置: {start_line}-{end_line}")
                    
                    method_info = {
                        'name': type_name,
                        'file_path': file_path,
                        'class_name': qualified_name,
                        'type': 'constructor',
                        'modifiers': set(constructor.modifiers) if hasattr(constructor, 'modifiers') else set(),
                        'signature': self._get_method_signature(constructor),
                        'start_line': start_line,
                        'end_line': end_line
                    }
                    self.method_index[constructor_name] = method_info
                    self.call_graph.add_method(constructor_name, method_info)
                    self.logger.debug(f"添加构造函数: {constructor_name}")
                
                # 处理普通方法和抽象方法
                for method in type_decl.methods:
                    method_name = f"{qualified_name}.{method.name}"
                    
                    # 确定方法类型
                    method_type = 'method'
                    modifiers = set(method.modifiers) if hasattr(method, 'modifiers') else set()
                    
                    if 'abstract' in modifiers:
                        method_type = 'abstract_method'
                    elif 'static' in modifiers:
                        method_type = 'static_method'
                    
                    # 获取方法的位置信息
                    start_line = method.position.line if hasattr(method, 'position') and method.position else None
                    end_line = self._find_node_end_line(method)
                    
                    self.logger.debug(f"方法位置: {start_line}-{end_line}")
                    
                    method_info = {
                        'name': method.name,
                        'file_path': file_path,
                        'class_name': qualified_name,
                        'type': method_type,
                        'modifiers': modifiers,
                        'signature': self._get_method_signature(method),
                        'start_line': start_line,
                        'end_line': end_line
                    }
                    self.method_index[method_name] = method_info
                    self.call_graph.add_method(method_name, method_info)
                    self.logger.debug(f"添加方法: {method_name} ({method_type})")

            self.logger.info(f"索引了 {len(self.method_index)} 个方法")

        except Exception as e:
            self.logger.error(f"处理文件时出错 {file_path}: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())

    def _find_node_end_line(self, node):
        """查找节点的结束行号"""
        try:
            if not hasattr(node, 'position') or not node.position:
                return None
            
            # 获取起始行号
            start_line = node.position.line
            
            # 遍历所有子节点，找到最大的行号
            max_line = start_line
            
            # 使用filter遍历所有子节点
            for _, child in node.filter(object):
                if hasattr(child, 'position') and child.position:
                    max_line = max(max_line, child.position.line)
                    
                # 如果子节点有token_end_pos属性，也考虑它
                if hasattr(child, 'token_end_pos') and child.token_end_pos:
                    max_line = max(max_line, child.token_end_pos[0])
            
            # 如果节点有token_end_pos属性，也考虑它
            if hasattr(node, 'token_end_pos') and node.token_end_pos:
                max_line = max(max_line, node.token_end_pos[0])
            
            return max_line
            
        except Exception as e:
            self.logger.error(f"查找节点结束行号时出错: {str(e)}")
            return None

    def _get_type_name(self, type_node):
        """获取类型的完整名称"""
        if type_node is None:
            return 'void'
        
        if isinstance(type_node, javalang.tree.BasicType):
            return type_node.name
        
        if isinstance(type_node, javalang.tree.ReferenceType):
            # 处理数组类型
            array_depth = len(type_node.dimensions) if hasattr(type_node, 'dimensions') else 0
            base_type = type_node.name if hasattr(type_node, 'name') else ''
            return base_type + '[]' * array_depth
        
        return str(type_node)

    def _resolve_variable_type(self, node, current_type):
        """解析变量类型"""
        try:
            # 如果是字段访问
            if isinstance(node, javalang.tree.MemberReference):
                # 检查是否是字段
                field_key = f"{current_type}.{node.member}"
                if field_key in self.field_types:
                    return self.field_types[field_key]
                
                # 检查父类的字段
                parent_type = self.class_cache.get(current_type, {}).get('superclass')
                while parent_type:
                    parent_field_key = f"{parent_type}.{node.member}"
                    if parent_field_key in self.field_types:
                        return self.field_types[parent_field_key]
                    parent_type = self.class_cache.get(parent_type, {}).get('superclass')
                
            # 如果是局部变量
            elif isinstance(node, javalang.tree.LocalVariableDeclaration):
                return self._get_type_name(node.type)
            
            # 如果是参数
            elif isinstance(node, javalang.tree.FormalParameter):
                return self._get_type_name(node.type)
            
            return None
            
        except Exception as e:
            self.logger.error(f"解析变量类型时出错: {str(e)}")
            return None

    def _add_method_to_index(self, node, type_name, file_path, method_type):
        """添加方法到索引"""
        try:
            # 对于构造函数，使用类名作为方法名
            if method_type == 'constructor':
                method_name = type_name.split('.')[-1]
            else:
                method_name = node.name
            
            qualified_name = f"{type_name}.{method_name}"
            
            # 处理方法重载
            if qualified_name in self.method_index:
                param_types = [self._get_type_name(p.type) for p in node.parameters] if hasattr(node, 'parameters') else []
                if param_types:
                    qualified_name = f"{qualified_name}({','.join(param_types)})"
            
            # 获取行号信息
            start_line = node.position.line if hasattr(node, 'position') and node.position else None
            end_line = None
            if hasattr(node, 'body') and node.body:
                last_statement = node.body[-1] if isinstance(node.body, list) else node.body
                if hasattr(last_statement, 'position'):
                    end_line = last_statement.position.line
            
            method_info = {
                'name': method_name,
                'qualified_name': qualified_name,
                'file_path': file_path,
                'class_name': type_name,
                'start_line': start_line,
                'end_line': end_line,
                'type': method_type,
                'modifiers': set(node.modifiers) if hasattr(node, 'modifiers') else set(),
                'parameters': self._get_method_parameters(node),
                'return_type': self._get_method_return_type(node) if method_type != 'constructor' else None,
                'throws': list(node.throws) if hasattr(node, 'throws') and node.throws else []
            }
            
            self.method_index[qualified_name] = method_info
            self.call_graph.add_method(qualified_name, method_info)
            self.logger.debug(f"添加{method_type}到索引: {qualified_name}")
            
        except Exception as e:
            self.logger.error(f"添加方法到索引时出错: {str(e)}")
            raise

    def _get_method_modifiers(self, node):
        """获取方法的修饰符集合
        
        Args:
            node: 方法节点（MethodDeclaration或ConstructorDeclaration）
            
        Returns:
            set: 修饰符集合，如 {'public', 'static', 'final'}
        """
        try:
            modifiers = set()
            if hasattr(node, 'modifiers'):
                modifiers.update(node.modifiers)
                
            # 如果是接口方法，默认添加public和abstract修饰符
            if (isinstance(node, javalang.tree.MethodDeclaration) and 
                isinstance(self._get_parent(node), javalang.tree.InterfaceDeclaration)):
                modifiers.add('public')
                modifiers.add('abstract')
                
            return modifiers
        except Exception as e:
            self.logger.error(f"获取方法修饰符时出错: {str(e)}")
            return set()

    def _get_method_signature(self, node):
        """获取方法的完整签名
        
        Args:
            node: 方法节点
            
        Returns:
            str: 方法签名，如 'public static void main(String[] args)'
        """
        try:
            # 获取修饰符
            modifiers = ' '.join(sorted(self._get_method_modifiers(node)))
            
            # 获取返回类型（构造函数没有返回类型）
            return_type = ''
            if isinstance(node, javalang.tree.MethodDeclaration):
                return_type = self._get_type_name(node.return_type)
                
            # 获取方法名
            name = node.name
            
            # 获取参数列表
            params = []
            for param in node.parameters:
                param_type = self._get_type_name(param.type)
                if param.varargs:
                    param_type += '...'
                params.append(f"{param_type} {param.name}")
                
            # 构建完整签名
            signature_parts = []
            if modifiers:
                signature_parts.append(modifiers)
            if return_type:
                signature_parts.append(return_type)
            signature_parts.append(name)
            signature_parts.append(f"({', '.join(params)})")
            
            # 添加throws子句
            if hasattr(node, 'throws') and node.throws:
                throws = [self._get_type_name(t) for t in node.throws]
                signature_parts.append(f"throws {', '.join(throws)}")
                
            return ' '.join(signature_parts)
            
        except Exception as e:
            self.logger.error(f"获取方法签名时出错: {str(e)}")
            return f"{node.name}()"

    def _get_parent(self, node, root=None):
        """获取节点的父节点
        
        Args:
            node: 当前节点
            root: 根节点（可选）
            
        Returns:
            node: 父节点，如果没有找到则返回None
        """
        try:
            if root is None:
                root = self.ast_data
                
            def find_parent(current, target, parent=None):
                if current is target:
                    return parent
                    
                if isinstance(current, (list, tuple)):
                    for item in current:
                        result = find_parent(item, target, current)
                        if result is not None:
                            return result
                            
                elif isinstance(current, dict):
                    for value in current.values():
                        result = find_parent(value, target, current)
                        if result is not None:
                            return result
                            
                elif hasattr(current, '__dict__'):
                    for value in current.__dict__.values():
                        result = find_parent(value, target, current)
                        if result is not None:
                            return result
                            
                return None
                
            return find_parent(root, node)
            
        except Exception as e:
            self.logger.error(f"获取父节点时出错: {str(e)}")
            return None

    def _get_method_parameters(self, node):
        """解析方法的参数列表
        
        Args:
            node: 方法节点
            
        Returns:
            list: 参数列表，每个参数是一个字典，包含类型和名称
        """
        try:
            params = []
            for param in node.parameters:
                param_type = self._get_type_name(param.type)
                if param.varargs:
                    param_type += '...'
                params.append({
                    'type': param_type,
                    'name': param.name
                })
            return params
        except Exception as e:
            self.logger.error(f"解析方法参数时出错: {str(e)}")
            return []

    def _get_method_return_type(self, node):
        """获取方法的返回类型
        
        Args:
            node: 方法节点
            
        Returns:
            str: 返回类型的完整名称
        """
        try:
            if isinstance(node, javalang.tree.ConstructorDeclaration):
                return 'void'
            return self._get_type_name(node.return_type)
        except Exception as e:
            self.logger.error(f"获取方法返回类型时出错: {str(e)}")
            return 'Object'

    def _process_file_calls(self, file_path):
        """处理单个文件中的方法调用"""
        try:
            # 获取当前类型名（类或接口）
            current_type = self._get_current_class(file_path)
            if not current_type:
                self.logger.warning(f"无法获取类型名: {file_path}")
                return

            # 检查当前类型是否有效
            if not current_type:
                self.logger.warning(f"无法获取当前类型: {file_path}")
                return None
            
            # 检查当前类型下是否有任何方法
            class_methods = [m for m in self.method_index.keys() if m.startswith(f"{current_type}.")]
            if not class_methods:
                self.logger.warning(f"当前类型 {current_type} 没有任何已索引的方法")
                # 不应该直接返回None，因为可能是新添加的类
                # 继续处理以捕获可能的方法调用

            # 解析文件
            with open(os.path.join(self.src_root, file_path), 'r', encoding='utf-8') as f:
                tree = javalang.parse.parse(f.read())

            # 获取所有字段的类型信息
            field_types = self._get_field_types(tree)
            
            self.logger.debug(f"\n开始处理文件的方法调用: {file_path}")
            self.logger.debug(f"当前类型: {current_type}")

            # 遍历所有方法调用
            for path, node in tree.filter(javalang.tree.MethodInvocation):
                try:
                    # 找到当前方法调用所在的方法声明
                    method_decl = self._find_parent_method(path)
                    if not method_decl:
                        self.logger.debug(f"找不到父方法: {node.member}")
                        continue

                    # 获取调用者方法的完整限定名
                    caller_method = f"{current_type}.{method_decl.name}"
                    
                    # 获取被调用方法的完整限定名
                    callee = self._resolve_method_call(node, current_type, field_types, self._get_cached_imports(file_path))
                    
                    if callee:
                        self.logger.debug(f"尝试添加调用关系: {caller_method} -> {callee}")
                        
                        # 检查调用者是否在method_index中
                        if caller_method not in self.method_index:
                            self.logger.warning(f"调用者方法不在method_index中: {caller_method}")
                            # 尝试添加调用者方法到method_index
                            method_info = {
                                'name': method_decl.name,
                                'file_path': file_path,
                                'class_name': current_type,
                                'type': 'method',
                                'modifiers': self._get_method_modifiers(method_decl),
                                'signature': self._get_method_signature(method_decl)
                            }
                            self.method_index[caller_method] = method_info
                            self.call_graph.add_method(caller_method, method_info)
                            self.logger.debug(f"已添加调用者方法到索引: {caller_method}")

                        # 检查被调用者是否在method_index中
                        if callee not in self.method_index:
                            self.logger.warning(f"被调用方法不在method_index中: {callee}")
                            # 可能是外部方法，仍然记录调用关系
                            self.logger.debug(f"记录对外部方法的调用: {callee}")
                            
                        # 添加调用关系
                        self.call_graph.add_call(caller_method, callee)
                        self.logger.debug(f"已添加调用关系: {caller_method} -> {callee}")

                except Exception as e:
                    self.logger.error(f"处理方法调用时出错: {str(e)}")
                    continue

            # 处理构造函数调用
            for path, node in tree.filter(javalang.tree.ClassCreator):
                try:
                    method_decl = self._find_parent_method(path)
                    if not method_decl:
                        continue

                    caller_method = f"{current_type}.{method_decl.name}"
                    callee_class = node.type.name
                    
                    # 解析完整的构造函数调用
                    if callee_class in field_types:
                        callee = f"{field_types[callee_class]}.{callee_class}"
                    else:
                        imports = self._get_cached_imports(file_path)
                        if callee_class in imports:
                            callee = f"{imports[callee_class]}.{callee_class}"
                        else:
                            current_package = current_type.rsplit('.', 1)[0]
                            callee = f"{current_package}.{callee_class}.{callee_class}"

                    if callee:
                        self.logger.debug(f"尝试添加构造函数调用: {caller_method} -> {callee}")
                        
                        # 检查调用者是否在method_index中
                        if caller_method not in self.method_index:
                            self.logger.warning(f"调用者方法不在method_index中: {caller_method}")
                            # 尝试添加调用者方法到method_index
                            method_info = {
                                'name': method_decl.name,
                                'file_path': file_path,
                                'class_name': current_type,
                                'type': 'method',
                                'modifiers': self._get_method_modifiers(method_decl),
                                'signature': self._get_method_signature(method_decl)
                            }
                            self.method_index[caller_method] = method_info
                            self.call_graph.add_method(caller_method, method_info)
                            self.logger.debug(f"已添加调用者方法到索引: {caller_method}")

                        # 添加调用关系
                        self.call_graph.add_call(caller_method, callee)
                        self.logger.debug(f"已添加构造函数调用关系: {caller_method} -> {callee}")

                except Exception as e:
                    self.logger.error(f"处理构造函数调用时出错: {str(e)}")
                    continue

        except Exception as e:
            self.logger.error(f"处理文件调用时出错 {file_path}: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())


    def _resolve_method_call(self, node, current_type, field_types, imports):
        try:
            member = node.member
            qualifier = node.qualifier
            
            # 检查是否是要排除的通用方法
            if member in self.exclude_methods:
                self.logger.debug(f"跳过通用方法: {member}")
                return None
            
            self.logger.debug(f"\n调试信息:")
            self.logger.debug(f"当前类型: {current_type}")
            self.logger.debug(f"方法成员: {member}")
            self.logger.debug(f"限定符: {qualifier}")
            self.logger.debug(f"限定符类型: {type(qualifier)}")
            self.logger.debug(f"字段类型映射: {field_types}")
            self.logger.debug(f"导入信息: {imports}")
            
            callee = None
            
            if isinstance(qualifier, str):
                self.logger.debug(f"字符串限定符: {qualifier}")
                if qualifier in field_types:
                    # 使用字段的声明类型
                    field_type = field_types[qualifier]
                    # 如果字段类型在导入中有对应的完整类名，使用完整类名
                    if field_type in imports:
                        callee = f"{imports[field_type]}.{member}"
                        self.logger.debug(f"字段类型调用(从导入): {callee}")
                    else:
                        callee = f"{field_type}.{member}"
                        self.logger.debug(f"字段类型调用(原始): {callee}")
                elif qualifier in imports:
                    callee = f"{imports[qualifier]}.{member}"
                    self.logger.debug(f"导入类型调用: {callee}")
                elif '.' in qualifier:
                    # 已经是完整限定名
                    callee = f"{qualifier}.{member}"
                    self.logger.debug(f"完整限定名调用: {callee}")
                else:
                    # 在同包中查找
                    current_package = current_type.rsplit('.', 1)[0]
                    callee = f"{current_package}.{qualifier}.{member}"
                    self.logger.debug(f"同包调用: {callee}")
            
            # 在返回之前检查是否是标准库调用
            if callee:
                # 检查是否是要排除的包
                if any(callee.startswith(prefix) for prefix in self.exclude_prefixes):
                    self.logger.debug(f"跳过标准库调用: {callee}")
                    return None
                
                # 移除多余的点号并返回
                callee = re.sub(r'\.+', '.', callee)
                callee = callee.strip('.')
                self.logger.debug(f"保留调用: {callee}")
            
            return callee

        except Exception as e:
            self.logger.error(f"解析方法调用时出错:")
            self.logger.error(f"异常类型: {type(e)}")
            self.logger.error(f"异常信息: {str(e)}")
            self.logger.error(f"当前类型: {current_type}")
            self.logger.error(f"节点信息: {vars(node)}")
            return None

    def analyze_file(self, file_path, modified_lines):
        """分析单个文件的修改"""
        try:
            self.logger.info(f"开始分析文件: {file_path}")
            self.logger.info(f"修改的行号: {modified_lines}")
            
            # 确保已建立项目索引
            if not self.method_index:
                self.build_project_index()
            
            # 查找受影响的方法
            affected_methods, method_line_map = self.find_methods_by_lines(file_path, modified_lines)
            
            if not affected_methods:
                self.logger.info(f"未找到受影响的方法: {file_path}")
                return {
                    'affected_methods': [],
                    'method_line_map': method_line_map,
                    'method_calls': {
                        'callers': {},
                        'callees': {}
                    }
                }
            
            self.logger.debug(f"受影响的方法: {affected_methods}")
            
            # 获取受影响方法的完整调用关系
            method_calls = self._get_complete_call_relations(affected_methods)
            
            result = {
                'affected_methods': affected_methods,
                'method_line_map': method_line_map,
                'method_calls': method_calls
            }
            
            self.logger.info(f"分析完成: {file_path}")
            self.logger.debug(f"分析结果: {result}")
            return result

        except Exception as e:
            self.logger.error(f"分析文件时出错 {file_path}: {str(e)}")
            return None

    def find_methods_by_lines(self, file_path, modified_lines):
        """
        根据修改的行号找出受影响的方法。
        
        Args:
            file_path: 文件路径
            modified_lines: 修改的行号列表
        
        Returns:
            tuple: (受影响的方法列表, 方法行号映射)
        """
        try:
            # 解析文件获取原始AST
            with open(os.path.join(self.src_root, file_path), 'r', encoding='utf-8') as f:
                source = f.read()
                tree = javalang.parse.parse(source)

            affected_methods = []
            method_line_map = {}
            current_type = self._get_current_class(file_path)
            
            if not current_type:
                self.logger.error(f"无法获取类型名: {file_path}")
                return [], {}

            # 处理普通方法
            for path, node in tree.filter(javalang.tree.MethodDeclaration):
                method_name = node.name
                qualified_name = f"{current_type}.{method_name}"
                
                # 获取方法的起始行和结束行
                start_line = node.position.line if node.position else None
                end_line = self._find_node_end_line(node)
                
                if start_line and end_line:
                    method_line_map[qualified_name] = {
                        'start_line': start_line,
                        'end_line': end_line
                    }
                    
                    # 检查是否有修改行落在这个方法范围内
                    for line in modified_lines:
                        if start_line <= line <= end_line:
                            affected_methods.append(qualified_name)
                            self.logger.debug(f"找到受影响的方法: {qualified_name} (行 {start_line}-{end_line})")
                            break

            # 处理构造函数
            for path, node in tree.filter(javalang.tree.ConstructorDeclaration):
                method_name = node.name
                qualified_name = f"{current_type}.{method_name}"
                
                # 获取构造函数的起始行和结束行
                start_line = node.position.line if node.position else None
                end_line = self._find_node_end_line(node)
                
                if start_line and end_line:
                    method_line_map[qualified_name] = {
                        'start_line': start_line,
                        'end_line': end_line
                    }
                    
                    # 检查是否有修改行落在这个构造函数范围内
                    for line in modified_lines:
                        if start_line <= line <= end_line:
                            affected_methods.append(qualified_name)
                            self.logger.debug(f"找到受影响的构造函数: {qualified_name} (行 {start_line}-{end_line})")
                            break

            self.logger.info(f"文件 {file_path} 中找到 {len(affected_methods)} 个受影响的方法")
            return list(set(affected_methods)), method_line_map

        except Exception as e:
            self.logger.error(f"查找受影响方法时出错 {file_path}: {str(e)}")
            return [], {}

    def _get_complete_call_relations(self, affected_methods):
        """获取方法的完整调用关系"""
        try:
            complete_calls = {
                'callers': {},
                'callees': {}
            }
            
            print("\n========= 开始获取方法的调用关系 =========")
            print(f"受影响的方法列表: {affected_methods}")
            
            for method_name in affected_methods:
                print(f"\n===== 处理受影响的方法: {method_name} =====")
                
                # 直接从调用图中获取调用关系
                if method_name in self.call_graph.edges:
                    callers = list(self.call_graph.edges[method_name]['callers'])
                    callees = list(self.call_graph.edges[method_name]['callees'])
                    
                    print(f"找到方法的调用关系:")
                    print(f"调用者: {callers}")
                    print(f"被调用者: {callees}")
                    
                    # 直接添加到结果中
                    complete_calls['callers'][method_name] = {'callers': callers}
                    complete_calls['callees'][method_name] = {'callees': callees}
                else:
                    print(f"✗ 在调用图中找不到方法: {method_name}")
                    complete_calls['callers'][method_name] = {'callers': []}
                    complete_calls['callees'][method_name] = {'callees': []}
            
            print("\n========= 调用关系获取完成 =========")
            return complete_calls
            
        except Exception as e:
            print(f"获取调用关系时出错: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                'callers': {},
                'callees': {}
            }

    def save_analysis_result(self, file_path, result, modified_lines):
        """
        将分析结果保存到JSON文件。

        Args:
            file_path (str): 被分析的Java文件路径
            result (dict): 分析结果字典

        Returns:
            str: 保存的结果文件路径，保存失败则返回None
        """
        try:
            # 生成输出文件名
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = os.path.basename(file_path)
            output_file = os.path.join(
                self.output_dir, 
                f"analysis_{file_name}_{timestamp}.json"
            )
            
            # 添加元数据
            result_with_metadata = {
                "metadata": {
                    "analyzed_file": file_path,
                    "analysis_time": timestamp,
                    "modified_lines": modified_lines
                },
                "analysis_result": result
            }
            
            # 保存为JSON文件
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(result_with_metadata, f, indent=2, ensure_ascii=False)
            self.logger.info(f"\n分析结果已保存到: {output_file}")
            return output_file
        except Exception as e:
            self.logger.error(f"保存分析结果时出错: {e}")
            return None

    def parse_diff(self, diff_text):
        """
        解析git diff文本，提取修改的文件和行号。

        Args:
            diff_text (str): git diff命令的输出文本

        Returns:
            dict: 文件路径到修改行号的映射
        """
        changes = {}
        current_file = None
        current_line_number = 0
        in_hunk = False
        
        # 使用正则表达式匹配diff头和块头
        file_pattern = re.compile(r'diff --git (?:src://)?(.+?) (?:dst://)?.*')
        hunk_pattern = re.compile(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@')
        
        for line in diff_text.splitlines():
            # 检查是否是新文件的开始
            file_match = file_pattern.match(line)
            if file_match:
                # 提取相对路径，移除可能的 src:// 前缀
                current_file = file_match.group(1)
                # 确保使用正确的路径分隔符并移除开头的 src/
                current_file = current_file.replace('\\', os.path.sep).replace('/', os.path.sep)
                if current_file.startswith('src' + os.path.sep):
                    current_file = current_file[4:]  # 移除开头的 'src/'
                self.logger.debug("处理文件: %s", current_file)
                changes[current_file] = {'modified_lines': set()}
                in_hunk = False
                continue
            
            # 检查是否是块头（@@ 标记）
            hunk_match = hunk_pattern.match(line)
            if hunk_match:
                in_hunk = True
                current_line_number = int(hunk_match.group(1))
                continue
            
            # 处理修改的行
            if in_hunk and current_file:
                if line.startswith('+') and not line.startswith('+++'):
                    changes[current_file]['modified_lines'].add(current_line_number)
                    current_line_number += 1
                elif line.startswith('-') and not line.startswith('---'):
                    # 对于删除的行，我们也记录相应位置
                    changes[current_file]['modified_lines'].add(current_line_number)
                elif not line.startswith('\\'):  # 忽略 "\ No newline at end of file"
                    current_line_number += 1

        # 将集合转换为排序后的列表
        for file_path in changes:
            changes[file_path]['modified_lines'] = sorted(list(changes[file_path]['modified_lines']))
            self.logger.debug("文件 %s 的修改行: %s", file_path, changes[file_path]['modified_lines'])
        
        return changes

    def _get_parent(self, node):
        """
        获取AST节点的父节点。
        由于javalang的AST不直接支持父节点引用，我们需要手动实现。

        Args:
            node (dict): 当前AST节点

        Returns:
            dict: 父节点，如果没有找到则返回None
        """
        def find_parent(current_node, target_node, parent=None):
            if not isinstance(current_node, (dict, list)):
                return None
                
            if current_node is target_node:
                return parent
                
            if isinstance(current_node, dict):
                for value in current_node.values():
                    result = find_parent(value, target_node, current_node)
                    if result is not None:
                        return result
            elif isinstance(current_node, list):
                for item in current_node:
                    result = find_parent(item, target_node, current_node)
                    if result is not None:
                        return result
            return None
                
        # 从AST根节点开始搜索
        return find_parent(self.ast_data, node)

    def _get_cached_imports(self, file_path):
        """获取缓存的导入信息
        
        Args:
            file_path: 源文件路径
            
        Returns:
            dict: 类名到完整限定名的映射
        """
        if file_path not in self.import_cache:
            try:
                # 从method_index中获取类型信息
                found_types = [t for t in self.method_index.items() if t[1].get('file_path') == file_path]
                if not found_types:
                    self.logger.warning(f"找不到文件对应的类型信息: {file_path}")
                    return {}
                    
                # 使用第一个找到的类型的包名和导入信息
                type_info = found_types[0][1]
                package_name = type_info.get('package')
                imports = type_info.get('imports', {})
                
                # 合并包名和导入信息
                imports.update({
                    '__package__': package_name  # 存储包名用于同包引用
                })
                
                self.import_cache[file_path] = imports
                self.logger.debug(f"已缓存导入信息: {file_path} -> {imports}")
                
            except Exception as e:
                self.logger.error(f"处理导入信息时出错 {file_path}: {str(e)}")
                self.import_cache[file_path] = {}
                
        return self.import_cache[file_path]

    def _get_cached_package(self, file_path):
        """获取缓存的包名"""
        if file_path not in self.import_cache:
            self._get_cached_imports(file_path)  # 这会同时缓存包名
        return self.import_cache[file_path].get('package')

    def _get_current_class(self, file_path):
        """获取当前文件的主类名（包括包名）"""
        try:
            with open(os.path.join(self.src_root, file_path), 'r', encoding='utf-8') as f:
                tree = javalang.parse.parse(f.read())

            # 获取包名
            package_name = None
            for _, node in tree.filter(javalang.tree.PackageDeclaration):
                if isinstance(node.name, list):
                    package_name = '.'.join(str(n.value) for n in node.name)
                else:
                    package_name = str(node.name)
                break

            self.logger.debug(f"包名: {package_name}")

            # 获取所有顶层类型声明
            declarations = []
            if hasattr(tree, 'types'):
                declarations.extend(tree.types)

            for declaration in declarations:
                # 获取类型名
                type_name = declaration.name
                qualified_name = f"{package_name}.{type_name}" if package_name else type_name
                
                # 记录类型信息
                type_info = {
                    'kind': type(declaration).__name__,
                    'modifiers': set(declaration.modifiers) if hasattr(declaration, 'modifiers') else set(),
                    'superclass': None,
                    'interfaces': [],
                    'file_path': file_path
                }

                # 处理继承关系
                if hasattr(declaration, 'extends'):
                    if declaration.extends:
                        if isinstance(declaration.extends, list):
                            type_info['interfaces'].extend(str(ext) for ext in declaration.extends)
                        else:
                            type_info['superclass'] = str(declaration.extends)

                # 处理接口实现
                if hasattr(declaration, 'implements'):
                    if declaration.implements:
                        type_info['interfaces'].extend(str(impl) for impl in declaration.implements)

                self.class_cache[qualified_name] = type_info
                self.logger.debug(f"找到类型: {qualified_name} ({type_info['kind']})")

                # 如果是顶层类型，返回其限定名
                return qualified_name

            self.logger.warning(f"在文件中未找到任何类型声明: {file_path}")
            return None

        except Exception as e:
            self.logger.error(f"获取当前类型名时出错 ({file_path}): {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None

    def _get_method_signature(self, method_node):
        """获取方法的签名
        
        Args:
            method_node: javalang.tree.MethodDeclaration 或 javalang.tree.ConstructorDeclaration
            
        Returns:
            str: 方法签名字符串
        """
        try:
            # 获取方法修饰符
            modifiers = method_node.modifiers if hasattr(method_node, 'modifiers') else set()
            modifiers_str = ' '.join(sorted(modifiers)) + ' ' if modifiers else ''
            
            # 获取返回类型（构造函数没有返回类型）
            return_type = ''
            if hasattr(method_node, 'return_type') and method_node.return_type:
                if hasattr(method_node.return_type, 'name'):
                    return_type = method_node.return_type.name + ' '
                else:
                    return_type = str(method_node.return_type) + ' '
            
            # 获取方法名
            name = method_node.name
            
            # 获取参数
            params = []
            if hasattr(method_node, 'parameters') and method_node.parameters:
                for param in method_node.parameters:
                    param_type = param.type.name if hasattr(param.type, 'name') else str(param.type)
                    param_name = param.name
                    params.append(f"{param_type} {param_name}")
            
            params_str = ', '.join(params)
            
            # 构建完整签名
            signature = f"{modifiers_str}{return_type}{name}({params_str})"
            return signature.strip()
            
        except Exception as e:
            self.logger.error(f"获取方法签名时出错: {str(e)}")
            return f"{method_node.name}()"  # 返回简单的备用签名

    def _get_field_types(self, tree):
        """获取类中所有字段的类型信息
        
        Args:
            tree: Java AST树
            
        Returns:
            dict: 字段名到类型的映射
        """
        field_types = {}
        try:
            # 遍历所有字段声明
            for _, node in tree.filter(javalang.tree.FieldDeclaration):
                # 获取字段类型
                field_type = None
                if isinstance(node.type, javalang.tree.ReferenceType):
                    field_type = node.type.name
                elif hasattr(node.type, 'value'):
                    field_type = node.type.value
                    
                # 获取所有声明的字段名
                for declarator in node.declarators:
                    if field_type:
                        field_types[declarator.name] = field_type
                        
            return field_types
            
        except Exception as e:
            self.logger.error(f"获取字段类型时出错: {str(e)}")
            return {}

    def _find_parent_method(self, path):
        """查找当前节点所在的方法声明
        
        Args:
            path: AST节点路径
            
        Returns:
            MethodDeclaration/ConstructorDeclaration: 父方法声明节点，如果没找到则返回None
        """
        try:
            # 从路径中查找方法声明
            for node in reversed(path):
                if isinstance(node, (javalang.tree.MethodDeclaration, javalang.tree.ConstructorDeclaration)):
                    return node
            return None
            
        except Exception as e:
            self.logger.error(f"查找父方法时出错: {str(e)}")
            return None

    def analyze_project(self, src_root):
        """分析项目源代码，构建调用图"""
        try:
            self.logger.info(f"开始分析项目: {src_root}")
            self.src_root = src_root
            
            # 获取所有Java文件
            java_files = []
            for root, _, files in os.walk(src_root):
                for file in files:
                    if file.endswith('.java'):
                        rel_path = os.path.relpath(os.path.join(root, file), src_root)
                        java_files.append(rel_path)
            
            self.logger.info(f"找到 {len(java_files)} 个Java文件")
            
            # 首先处理所有文件以建立method_index
            for file_path in java_files:
                self.logger.debug(f"\n处理文件: {file_path}")
                if 'package-info.java' in file_path:
                    self.logger.debug(f"跳过package-info文件: {file_path}")
                    continue
                self._process_file(file_path)
                
            self.logger.info(f"method_index中共有 {len(self.method_index)} 个方法")
            
            # 输出method_index的内容用于调试
            self.logger.debug("\nmethod_index内容:")
            for method_name, info in self.method_index.items():
                self.logger.debug(f"  {method_name}: {info}")
                
            # 再次遍历处理方法调用
            for file_path in java_files:
                if 'package-info.java' in file_path:
                    continue
                self.logger.debug(f"\n处理文件的方法调用: {file_path}")
                self._process_file_calls(file_path)
                
            # 输出调用图信息
            self.logger.info(f"调用图构建完成，共有 {len(self.call_graph.edges)} 个方法的调用关系")
            
            # 输出一些调用关系示例
            self.logger.debug("\n调用关系示例:")
            count = 0
            for method, calls in self.call_graph.edges.items():
                if calls['callees']:
                    self.logger.debug(f"  {method} 调用了:")
                    for callee in calls['callees']:
                        self.logger.debug(f"    -> {callee}")
                    count += 1
                    if count >= 5:  # 只显示前5个有调用的方法
                        break
                    
            return self.call_graph
            
        except Exception as e:
            self.logger.error(f"分析项目时出错: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None
