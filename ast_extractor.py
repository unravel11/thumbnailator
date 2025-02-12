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
        
        # 添加新的缓存用于跟踪局部变量
        self.local_var_types = {}  # 缓存方法内的局部变量类型
        self.method_local_vars = {}  # 按方法缓存局部变量

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
        self.local_var_types = {}
        self.method_local_vars = {}

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
            
            # 修改导入处理逻辑
            # 1. 处理显式导入
            for _, node in tree.filter(javalang.tree.Import):
                if node.path:
                    if isinstance(node.path, list):
                        import_path = '.'.join(str(p.value) if hasattr(p, 'value') else str(p) for p in node.path)
                    else:
                        import_path = str(node.path)
                    
                    # 处理静态导入和普通导入
                    if node.static:
                        # 静态导入
                        class_name = '.'.join(import_path.split('.')[:-1])
                        method_name = import_path.split('.')[-1]
                        imports[method_name] = {'type': 'static', 'class': class_name, 'member': method_name}
                    else:
                        # 普通导入
                        if '*' in import_path:
                            # 导入整个包
                            package = import_path.replace('.*', '')
                            imports[package] = {'type': 'package', 'package': package}
                        else:
                            # 导入具体类
                            simple_name = import_path.split('.')[-1]
                            imports[simple_name] = {'type': 'class', 'fqn': import_path}
                            # 同时保存字符串形式，用于向后兼容
                            imports[simple_name] = import_path

            # 2. 添加隐式导入
            imports['java.lang'] = {'type': 'package', 'package': 'java.lang'}
            
            # 将包名添加到导入信息中
            imports['__package__'] = package_name
            self.import_cache[normalized_path] = imports
            
            # 获取所有字段的类型信息
            field_types = {}
            for path, field_decl in tree.filter(javalang.tree.FieldDeclaration):
                # 获取字段类型
                field_type = self._resolve_type_name(field_decl.type, imports, package_name)
                
                # 处理每个字段声明
                for declarator in field_decl.declarators:
                    field_name = declarator.name
                    field_types[field_name] = field_type
                    self.logger.debug(f"添加字段类型: {field_name} -> {field_type}")
                    
                    # 如果有初始化器，也处理它
                    if declarator.initializer:
                        if isinstance(declarator.initializer, javalang.tree.MethodInvocation):
                            if (declarator.initializer.arguments and 
                                isinstance(declarator.initializer.arguments[0], javalang.tree.ClassReference)):
                                class_ref = declarator.initializer.arguments[0]
                                creator_type = class_ref.type.name
                                resolved_type = self._resolve_type_name(creator_type, imports, package_name)
                                field_types[field_name] = resolved_type
                                self.logger.debug(f"从工厂方法推断字段类型: {field_name} -> {resolved_type}")
            
            # 在处理方法声明之前，先处理所有导入
            for _, node in tree.filter(javalang.tree.Import):
                if node.path:
                    if isinstance(node.path, list):
                        import_path = '.'.join(str(p.value) if hasattr(p, 'value') else str(p) for p in node.path)
                    else:
                        import_path = str(node.path)
                    simple_name = import_path.split('.')[-1]
                    imports[simple_name] = import_path
            
            # 在处理方法声明之前添加局部变量类型分析
            for path, method_decl in tree.filter(javalang.tree.MethodDeclaration):
                # 获取完整的方法名
                parent_class = self._find_parent_class(path)
                if not parent_class:
                    self.logger.warning(f"找不到方法 {method_decl.name} 的父类，跳过处理")
                    continue
                
                # 构建完整的方法名
                current_type = f"{package_name}.{parent_class.name}"
                method_name = f"{current_type}.{method_decl.name}"
                
                self.logger.debug(f"\n=== 处理方法: {method_name} ===")
                
                # 初始化方法的局部变量映射
                method_vars = {}
                
                # 处理方法参数
                if method_decl.parameters:
                    for param in method_decl.parameters:
                        param_type = self._resolve_type_name(param.type, imports, package_name)
                        method_vars[param.name] = param_type
                        self.logger.debug(f"添加方法参数: {param.name} -> {param_type}")
                
                # 处理方法体中的局部变量
                if method_decl.body:
                    for statement in method_decl.body:
                        self._process_statement(statement, method_vars, imports, package_name)
                
                # 存储方法的局部变量信息
                self.method_local_vars[method_name] = method_vars
                self.logger.debug(f"存储方法局部变量: {method_name} -> {method_vars}")

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
                    'imports': imports
                }
                self.method_index[qualified_name] = type_info
                
                # 处理构造函数
                for constructor in type_decl.constructors:
                    self._add_method_to_index(constructor, qualified_name, file_path, 'constructor')
                
                # 处理普通方法和抽象方法
                for method in type_decl.methods:
                    method_type = 'method'
                    if 'abstract' in method.modifiers:
                        method_type = 'abstract_method'
                    elif 'static' in method.modifiers:
                        method_type = 'static_method'
                    
                    self._add_method_to_index(method, qualified_name, file_path, method_type)

            self.logger.info(f"索引了 {len(self.method_index)} 个方法")

        except Exception as e:
            self.logger.error(f"处理文件时出错 {file_path}: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            raise

    def _process_statement(self, statement, method_vars, imports, package_name):
        """处理语句中的局部变量声明和初始化"""
        if isinstance(statement, javalang.tree.LocalVariableDeclaration):
            self.logger.debug(f"\n处理变量声明: {statement}")
            
            # 获取变量类型
            var_type = self._resolve_type_name(statement.type, imports, package_name)
            
            for declarator in statement.declarators:
                if declarator.initializer:
                    if isinstance(declarator.initializer, javalang.tree.ClassCreator):
                        # 处理new对象
                        creator_type = declarator.initializer.type.name
                        resolved_type = self._resolve_type_name(creator_type, imports, package_name)
                        method_vars[declarator.name] = resolved_type
                        self.logger.debug(f"从对象创建推断类型: {declarator.name} -> {resolved_type}")
                    elif isinstance(declarator.initializer, javalang.tree.MethodInvocation):
                        # 处理工厂方法
                        if (declarator.initializer.arguments and 
                            isinstance(declarator.initializer.arguments[0], javalang.tree.ClassReference)):
                            class_ref = declarator.initializer.arguments[0]
                            creator_type = class_ref.type.name
                            resolved_type = self._resolve_type_name(creator_type, imports, package_name)
                            method_vars[declarator.name] = resolved_type
                            self.logger.debug(f"从工厂方法推断类型: {declarator.name} -> {resolved_type}")
                        else:
                            method_vars[declarator.name] = var_type
                            self.logger.debug(f"使用声明类型: {declarator.name} -> {var_type}")
                else:
                    method_vars[declarator.name] = var_type
                    self.logger.debug(f"添加局部变量: {declarator.name} -> {var_type}")
        
        # 递归处理语句块
        if isinstance(statement, javalang.tree.BlockStatement):
            if hasattr(statement, 'statements') and statement.statements:
                for stmt in statement.statements:
                    self._process_statement(stmt, method_vars, imports, package_name)

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
                'throws': list(node.throws) if hasattr(node, 'throws') and node.throws else [],
                'signature': self._get_method_signature(node)
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
            node: javalang.tree.MethodDeclaration 或 javalang.tree.ConstructorDeclaration
            
        Returns:
            str: 方法签名，如 'public static void main(String[] args)'
        """
        try:
            # 获取修饰符
            modifiers = node.modifiers if hasattr(node, 'modifiers') else set()
            modifiers_str = ' '.join(sorted(modifiers))
            
            # 获取返回类型（构造函数没有返回类型）
            return_type = ''
            if isinstance(node, javalang.tree.MethodDeclaration):
                return_type = self._get_type_name(node.return_type)
            
            # 获取方法名
            name = node.name
            
            # 获取参数列表
            params = []
            if hasattr(node, 'parameters') and node.parameters:
                for param in node.parameters:
                    param_type = self._get_type_name(param.type)
                    if param.varargs:
                        param_type += '...'
                    params.append(f"{param_type} {param.name}")
            
            # 构建完整签名
            signature_parts = []
            if modifiers_str:
                signature_parts.append(modifiers_str)
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
            self.logger.error(f"节点信息: {node}")
            return f"{node.name}()"  # 返回简单的备用签名

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
                    method_decl = self._find_parent_method(path)
                    if not method_decl:
                        continue
                    
                    caller_method = f"{current_type}.{method_decl.name}"
                    # 获取当前方法的局部变量
                    method_vars = self.method_local_vars.get(caller_method, {})
                    # 解析方法调用，传入局部变量信息
                    callee = self._resolve_method_call(node, current_type, field_types, self._get_cached_imports(file_path), method_vars)
                    
                    if callee:
                        self.logger.debug(f"尝试添加调用关系: {caller_method} -> {callee}")
                        
                        # 检查调用者是否在method_index中
                        if caller_method not in self.method_index:
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


    def _resolve_method_call(self, node, current_type, field_types, imports, method_vars):
        """解析方法调用"""
        try:
            member = node.member
            qualifier = node.qualifier
            
            # 定义常见的Java标准库类型
            common_java_types = {
                # 基础类型
                'Object', 'String', 'Integer', 'Long', 'Double', 'Float', 'Boolean', 'Byte', 'Short', 'Character',
                
                # 异常类型
                'Exception', 'RuntimeException', 'IllegalArgumentException', 'NullPointerException',
                'IllegalStateException', 'UnsupportedOperationException', 'IndexOutOfBoundsException',
                'NoSuchElementException', 'ClassCastException', 'ArrayIndexOutOfBoundsException',
                
                # 集合类型
                'List', 'ArrayList', 'LinkedList', 'Set', 'HashSet', 'Map', 'HashMap', 'TreeMap',
                'Collection', 'Collections', 'Arrays', 'Iterator', 'Iterable',
                
                # 其他常用类型
                'StringBuilder', 'StringBuffer', 'Math', 'System', 'Class', 'Thread', 'Runnable',
                'Optional', 'Stream', 'Collectors', 'Objects'
            }
            
            self.logger.debug(f"\n=== 解析方法调用 ===")
            self.logger.debug(f"当前类型: {current_type}")
            self.logger.debug(f"方法名: {member}")
            self.logger.debug(f"限定符: {qualifier}")
            self.logger.debug(f"字段类型: {field_types}")
            
            def resolve_qualifier_type(qual):
                if isinstance(qual, str):
                    # 0. 检查是否是常见Java类型
                    if qual in common_java_types:
                        self.logger.debug(f"跳过Java标准库类型: {qual}")
                        return f"java.lang.{qual}"
                    
                    # 1. 检查局部变量
                    if qual in method_vars:
                        var_type = method_vars[qual]
                        self.logger.debug(f"找到局部变量类型: {qual} -> {var_type}")
                        return var_type
                    
                    # 2. 检查字段
                    elif qual in field_types:
                        field_type = field_types[qual]
                        self.logger.debug(f"找到字段类型: {qual} -> {field_type}")
                        return field_type
                    
                    # 3. 检查是否是类名(静态方法调用)
                    elif qual in imports:
                        class_name = imports[qual]
                        self.logger.debug(f"找到类型导入: {qual} -> {class_name}")
                        return class_name
                    else:
                        # 检查是否是标准库类
                        standard_lib_classes = {
                            'Object': 'java.lang.Object',
                            'String': 'java.lang.String',
                            'Integer': 'java.lang.Integer',
                            'Long': 'java.lang.Long',
                            'Double': 'java.lang.Double',
                            'Float': 'java.lang.Float',
                            'Boolean': 'java.lang.Boolean',
                            'Byte': 'java.lang.Byte',
                            'Short': 'java.lang.Short',
                            'Character': 'java.lang.Character',
                            'System': 'java.lang.System',
                            'Thread': 'java.lang.Thread',
                            'Exception': 'java.lang.Exception',
                            'RuntimeException': 'java.lang.RuntimeException',
                            'Throwable': 'java.lang.Throwable',
                            'Class': 'java.lang.Class',
                            'Math': 'java.lang.Math',
                            'StringBuilder': 'java.lang.StringBuilder',
                            'StringBuffer': 'java.lang.StringBuffer'
                        }
                        
                        # 如果限定符包含点号，可能是标准库的静态字段引用
                        if '.' in qual:
                            parts = qual.split('.')
                            if parts[0] in standard_lib_classes:
                                full_name = f"{standard_lib_classes[parts[0]]}.{'.'.join(parts[1:])}"
                                self.logger.debug(f"解析为标准库静态字段引用: {full_name}")
                                return full_name
                        
                        # 检查是否是标准库类
                        if qual in standard_lib_classes:
                            self.logger.debug(f"解析为标准库类: {standard_lib_classes[qual]}")
                            return standard_lib_classes[qual]
                            
                        # 如果不是标准库类，尝试解析为同包下的类
                        current_package = current_type.rsplit('.', 1)[0]
                        possible_class = f"{current_package}.{qual}"
                        self.logger.debug(f"尝试解析为同包类: {possible_class}")
                        return possible_class
                    
                return None
            
            # 解析限定符的类型
            qualifier_type = resolve_qualifier_type(qualifier)
            if qualifier_type:
                callee = f"{qualifier_type}.{member}"
                self.logger.debug(f"解析出的方法调用: {callee}")
                
                # 规范化调用名称
                callee = re.sub(r'\.+', '.', callee)
                callee = callee.strip('.')
                return callee
            
            # 如果没有限定符，检查是否是Java标准库类型的直接调用
            if not qualifier and member in common_java_types:
                self.logger.debug(f"跳过Java标准库类型的直接调用: {member}")
                return f"java.lang.{member}"
            
            return None
            
        except Exception as e:
            self.logger.error(f"解析方法调用时出错: {str(e)}")
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
                elif not line.startswith('\ No newline at end of file'):  # 忽略 "\ No newline at end of file"
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

    def _find_parent_method(self, path):
        """查找当前节点所在的方法声明
        
        Args:
            path: AST节点路径
            
        Returns:
            MethodDeclaration/ConstructorDeclaration: 父方法声明节点，如果没找到则返回None
        """
        try:
            if not path:
                self.logger.warning("AST 路径为空，无法查找父方法")
                return None

            # 从路径中查找方法声明
            for node in reversed(path):
                if isinstance(node, javalang.tree.MethodDeclaration):
                    return node
                elif isinstance(node, javalang.tree.ConstructorDeclaration):
                    return node
                elif isinstance(node, javalang.tree.LambdaExpression):
                    return node  # Lambda 也可能是一个方法上下文

            self.logger.debug("未找到父方法声明")
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

    def _resolve_type_name(self, type_node, imports, package_name):
        """解析完整的类型名称"""
        try:
            self.logger.debug("\n=== 解析类型名称 ===")
            self.logger.debug(f"输入类型: {type_node}")
            self.logger.debug(f"导入信息: {imports}")
            self.logger.debug(f"包名: {package_name}")
            
            if isinstance(type_node, str):
                type_name = type_node
                
                # 1. 基本类型直接返回
                if type_name in {'byte', 'short', 'int', 'long', 'float', 'double', 'boolean', 'char'}:
                    return type_name
                    
                # 2. 已经是完整限定名
                if '.' in type_name:
                    return type_name
                    
                # 3. 检查导入
                if type_name in imports:
                    import_info = imports[type_name]
                    if isinstance(import_info, dict):
                        if import_info['type'] == 'class':
                            return import_info['fqn']
                        elif import_info['type'] == 'package':
                            return f"{import_info['package']}.{type_name}"
                    elif isinstance(import_info, str):
                        return import_info
                    
                # 4. java.lang包中的类
                if type_name in {'String', 'Object', 'Integer', 'Boolean', 'Double', 'Float', 'Exception', 'RuntimeException'}:
                    return f"java.lang.{type_name}"
                    
                # 5. 同包类型
                if package_name:
                    return f"{package_name}.{type_name}"
                    
                return type_name
                
            # 处理AST节点类型
            if isinstance(type_node, javalang.tree.BasicType):
                return type_node.name
                
            if isinstance(type_node, javalang.tree.ReferenceType):
                # 递归处理基础类型
                base_type = self._resolve_type_name(type_node.name, imports, package_name)
                # 处理数组维度
                array_dims = '[]' * len(type_node.dimensions) if hasattr(type_node, 'dimensions') else ''
                return base_type + array_dims
                
            return str(type_node)
            
        except Exception as e:
            self.logger.error(f"解析类型名称时出错: {str(e)}")
            return str(type_node)

    def _find_parent_class(self, path):
        """查找当前路径中的类声明节点"""
        try:
            for node in reversed(path):
                if isinstance(node, (javalang.tree.ClassDeclaration, javalang.tree.InterfaceDeclaration)):
                    return node
                
            self.logger.warning("在路径中未找到类或接口声明")
            return None
            
        except Exception as e:
            self.logger.error(f"查找父类时出错: {str(e)}")
            return None

    def _get_field_types(self, tree):
        """获取类中所有字段的类型信息
        
        Args:
            tree: Java AST树
            
        Returns:
            dict: 字段名到类型的映射，如 {'name': 'java.lang.String'}
        """
        try:
            field_types = {}
            imports = self._get_imports(tree)
            package_name = self._get_package_name(tree)
            
            # 遍历所有字段声明
            for path, field_decl in tree.filter(javalang.tree.FieldDeclaration):
                # 获取字段类型
                field_type = self._resolve_type_name(field_decl.type, imports, package_name)
                
                # 处理每个字段声明器
                for declarator in field_decl.declarators:
                    field_name = declarator.name
                    field_types[field_name] = field_type
                    
                    # 处理初始化器中的类型信息
                    if declarator.initializer:
                        if isinstance(declarator.initializer, javalang.tree.MethodInvocation):
                            # 处理工厂方法调用
                            if (declarator.initializer.arguments and 
                                isinstance(declarator.initializer.arguments[0], javalang.tree.ClassReference)):
                                class_ref = declarator.initializer.arguments[0]
                                creator_type = class_ref.type.name
                                resolved_type = self._resolve_type_name(creator_type, imports, package_name)
                                field_types[field_name] = resolved_type
                
                    self.logger.debug(f"添加字段类型: {field_name} -> {field_types[field_name]}")
            
            return field_types
            
        except Exception as e:
            self.logger.error(f"获取字段类型时出错: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {}

    def _get_imports(self, tree):
        """获取文件的导入信息
        
        Args:
            tree: Java AST树
            
        Returns:
            dict: 导入信息映射
        """
        imports = {}
        
        # 处理导入声明
        for _, node in tree.filter(javalang.tree.Import):
            if node.path:
                if isinstance(node.path, list):
                    import_path = '.'.join(str(p.value) if hasattr(p, 'value') else str(p) for p in node.path)
                else:
                    import_path = str(node.path)
                
                # 处理静态导入和普通导入
                if node.static:
                    class_name = '.'.join(import_path.split('.')[:-1])
                    method_name = import_path.split('.')[-1]
                    imports[method_name] = {'type': 'static', 'class': class_name, 'member': method_name}
                else:
                    if '*' in import_path:
                        package = import_path.replace('.*', '')
                        imports[package] = {'type': 'package', 'package': package}
                    else:
                        simple_name = import_path.split('.')[-1]
                        imports[simple_name] = {'type': 'class', 'fqn': import_path}
        
        # 添加 java.lang 包的隐式导入
        imports['java.lang'] = {'type': 'package', 'package': 'java.lang'}
        
        return imports

    def _get_package_name(self, tree):
        """获取文件的包名
        
        Args:
            tree: Java AST树
            
        Returns:
            str: 包名，如果没有则返回None
        """
        for _, node in tree.filter(javalang.tree.PackageDeclaration):
            if isinstance(node.name, list):
                return '.'.join(str(n.value) for n in node.name if hasattr(n, 'value'))
            else:
                return str(node.name)
        return None
