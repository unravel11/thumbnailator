# -*- coding: utf-8 -*-
import javalang
import json
import os
import datetime
import re
import logging
import argparse

"""
Java代码修改分析工具

输出JSON格式说明：
{
    "metadata": {
        "analysis_time": "20250120_154947",  # 分析时间
        "total_files": 2                      # 分析的文件总数
    },
    "file_analyses": {
        "src/main/java/.../Colorize.java": {  # 文件路径
            "affected_methods": [              # 受影响的方法列表
                {
                    "name": "Colorize",        # 方法名
                    "type": "ConstructorDeclaration",  # 方法类型
                    "lines": [95],             # 包含的修改行
                    "start_line": 82,          # 方法开始行
                    "end_line": 95,            # 方法结束行
                    "modifiers": ["public"]    # 方法修饰符
                },
                {
                    "name": "apply",
                    "type": "MethodDeclaration",
                    "lines": [113],
                    "start_line": 98,
                    "end_line": 114,
                    "modifiers": ["public"]
                }
            ],
            "method_line_map": {              # 方法到行号的映射
                "Colorize": {
                    "name": "Colorize",
                    "type": "ConstructorDeclaration",
                    "lines": [95],
                    "start_line": 82,
                    "end_line": 95,
                    "modifiers": []
                },
                "apply": {
                    "name": "apply",
                    "type": "MethodDeclaration",
                    "lines": [113],
                    "start_line": 98,
                    "end_line": 114,
                    "modifiers": []
                }
            },
            "method_calls": {                 # 方法调用关系
                "callers": {                  # 记录方法被谁调用
                    "Colorize": {
                        "file_path": "src/main/java/.../Colorize.java",
                        "method_code": "public Colorize(Color c, int alpha) {...}",
                        "start_line": 82,
                        "end_line": 95,
                        "type": "ConstructorDeclaration",
                        "modifiers": ["public"],
                        "callers": []         # 调用此方法的方法列表
                    },
                    "apply": {
                        "callers": [          # 被其他方法调用的记录
                            {
                                "name": "someMethod",
                                "line": 42,
                                "type": "method",
                                "qualifier": "instance"
                            }
                        ]
                    }
                },
                "callees": {                  # 记录方法调用了谁
                    "Colorize": {
                        "callees": [          # 此方法调用的其他方法
                            {
                                "name": "getRed",
                                "line": 89,
                                "type": "method",
                                "qualifier": "c"
                            },
                            {
                                "name": "getGreen",
                                "line": 90,
                                "type": "method",
                                "qualifier": "c"
                            }
                        ]
                    },
                    "apply": {
                        "callees": [
                            {
                                "name": "getWidth",
                                "line": 99,
                                "type": "method",
                                "qualifier": "img"
                            }
                        ]
                    }
                }
            }
        },
        "src/main/java/.../Flip.java": {
            // 结构同上
        }
    }
}

字段说明：
1. metadata: 分析元数据
   - analysis_time: 分析执行的时间戳
   - total_files: 分析的文件数量

2. file_analyses: 每个文件的分析结果
   - affected_methods: 包含修改行的方法列表
     - name: 方法名
     - type: 方法类型（构造函数/普通方法/Lambda等）
     - lines: 包含的修改行号
     - start_line/end_line: 方法的开始和结束行
     - modifiers: 方法的修饰符列表

3. method_line_map: 方法到行号的详细映射
   - 键为方法名
   - 值包含方法的详细信息（同affected_methods）

4. method_calls: 方法调用关系
   - callers: 记录每个方法被谁调用
     - file_path: 方法所在文件
     - method_code: 方法的完整代码
     - start_line: 开始行号
     - end_line: 结束行号
     - type: 方法类型
     - modifiers: 修饰符
     - callers: 调用此方法的方法列表
   - callees: 记录每个方法调用了哪些其他方法
     - callees: 被调用的方法列表
       - name: 被调用方法名
       - line: 调用发生的行号
       - type: 调用类型
       - qualifier: 调用方限定符
"""

class JavaASTExtractor:
    """Java代码AST分析器，用于分析Java代码的方法调用关系和修改影响。"""

    def __init__(self, logger=None):
        """
        初始化AST分析器。
        Args:
            logger: 共享的日志记录器，如果为None则创建新的
        """
        self.ast_data = {}
        self.src_root = None  # 源代码根目录
        # 创建输出目录
        self.output_dir = "analysis_results"
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            
        # 使用传入的logger或创建新的
        self.logger = logger or self._setup_logger()

    def _setup_logger(self):
        """仅在没有传入logger时使用此方法创建新的logger"""
        logger = logging.getLogger('JavaAnalyzer.AST')
        return logger

    def parse_file(self, file_path):
        """
        解析单个Java文件的AST。

        Args:
            file_path (str): Java文件的相对路径或绝对路径

        Returns:
            dict: 解析后的AST字典，解析失败则返回None
        """
        try:
            # 移除路径中的 'src://' 前缀和开头的 'src/'
            file_path = file_path.replace('src://', '')
            if file_path.startswith('src/'):
                file_path = file_path[4:]  # 移除开头的 'src/'

            # 构建完整路径
            full_path = os.path.join(self.src_root, file_path)
            self.logger.debug(f"解析文件: {full_path}")
            
            if not os.path.exists(full_path):
                self.logger.error(f"文件不存在: {full_path}")
                return None

            with open(full_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
                tree = javalang.parse.parse(source_code)
                return self.get_ast_dict(tree)
        except Exception as e:
            self.logger.error(f"解析文件出错: {full_path} - {str(e)}")
            return None

    def get_ast_dict(self, tree):
        """
        将javalang的AST树转换为字典格式。

        Args:
            tree: javalang解析出的AST树对象

        Returns:
            dict/list: 转换后的AST字典或列表
        """
        if isinstance(tree, list):
            return [self.get_ast_dict(node) for node in tree]
        elif isinstance(tree, set):
            return [self.get_ast_dict(node) for node in tree]
        elif isinstance(tree, javalang.tree.Node):
            node_dict = {'type': tree.__class__.__name__}
            # 添加位置信息
            if hasattr(tree, 'position') and tree.position:
                node_dict['position'] = {
                    'line': tree.position.line,
                    'column': tree.position.column
                }
            for attr in tree.__dict__:
                if attr != 'position':  # 跳过已处理的位置信息
                    value = getattr(tree, attr)
                    if isinstance(value, (javalang.tree.Node, list, set)):
                        node_dict[attr] = self.get_ast_dict(value)
                    else:
                        try:
                            json.dumps(value)
                            node_dict[attr] = value
                        except (TypeError, OverflowError):
                            node_dict[attr] = str(value)
            return node_dict
        return None

    def analyze_file(self, file_path, modified_lines):
        """
        分析单个文件中的修改。

        Args:
            file_path (str): 要分析的Java文件路径
            modified_lines (list): 修改的行号列表

        Returns:
            dict: 分析结果
        """
        self.logger.info(f"分析文件: {file_path}")
        ast = self.parse_file(file_path)
        if not ast:
            return None

        affected_methods, method_line_map = self.find_methods_by_lines(ast, modified_lines)
        if not affected_methods:
            return None

        # 分析方法调用关系
        method_calls = self.analyze_method_calls(ast, affected_methods, file_path)
        
        return {
            'affected_methods': affected_methods,
            'method_line_map': method_line_map,
            'method_calls': method_calls
        }

    def find_methods_by_lines(self, ast_node, lines):
        """
        找出包含指定行号的所有方法。

        Args:
            ast_node (dict): AST节点
            lines (list): 要查找的行号列表

        Returns:
            tuple: (methods, method_line_map)
        """
        methods = []
        method_line_map = {}
        
        # 修复日志格式化
        self.logger.info("需要分析的行号: %s", lines)
        self.logger.info("AST根节点类型: %s", type(ast_node))
        if isinstance(ast_node, dict):
            self.logger.info("根节点键: %s", list(ast_node.keys()))
            if 'type' in ast_node:
                self.logger.info("根节点类型: %s", ast_node['type'])
        
        def traverse(node, parent_method=None):
            if not isinstance(node, (dict, list)):
                return
                
            if isinstance(node, dict):
                node_type = node.get('type')
                if not node_type:
                    for value in node.values():
                        traverse(value, parent_method)
                    return
                    
                # 处理所有可能的方法类型声明
                if node_type in [
                    'MethodDeclaration',      # 普通方法
                    'ConstructorDeclaration', # 构造函数
                    'StaticInitializer',      # 静态初始化块
                    'InitializerDeclaration', # 实例初始化块
                    'LambdaExpression',       # Lambda表达式
                    'AnnotationMethodDeclaration'  # 注解中的方法
                ]:
                    method_name = node.get('name', '<anonymous>')
                    # 对于匿名方法，生成一个唯一标识符
                    if method_name == '<anonymous>':
                        if node_type == 'StaticInitializer':
                            method_name = '<static-initializer>'
                        elif node_type == 'InitializerDeclaration':
                            method_name = '<instance-initializer>'
                        elif node_type == 'LambdaExpression':
                            # 尝试从上下文获取lambda表达式的位置信息
                            pos = node.get('position', {})
                            if pos:
                                method_name = f'<lambda-{pos.get("line", "unknown")}>'
                    
                    pos = node.get('position', {})
                    if pos:
                        start_line = pos.get('line', 0)
                        end_line = self.find_method_end_line(node)
                        self.logger.debug(f"检查{node_type} {method_name}: 行 {start_line} 到 {end_line}")
                        
                        # 检查是否包含修改的行
                        contained_lines = []
                        for line in lines:
                            if start_line <= line <= end_line:
                                self.logger.debug(f"行号 {line} 在{node_type} {method_name} 中")
                                contained_lines.append(line)
                        
                        if contained_lines:
                            method_info = {
                                'name': method_name,
                                'type': node_type,
                                'lines': contained_lines,
                                'start_line': start_line,
                                'end_line': end_line,
                                'modifiers': self.get_method_modifiers(node)
                            }
                            methods.append(method_info)
                            method_line_map[method_name] = method_info
                            self.logger.debug(f"添加{node_type} {method_name} 到受影响方法列表，包含修改行: {contained_lines}")

                for value in node.values():
                    traverse(value, parent_method)
            elif isinstance(node, list):
                for item in node:
                    traverse(item, parent_method)

        traverse(ast_node)
        self.logger.info("\n分析结果:")
        self.logger.info(f"找到的受影响方法和构造函数: {[m['name'] for m in methods]}")
        self.logger.info("每个方法/构造函数包含的修改行:")
        for method_info in methods:
            self.logger.info(f"- {method_info['name']} ({method_info['type']}):")
            self.logger.info(f"  行: {method_info['lines']} (范围: {method_info['start_line']}-{method_info['end_line']})")
            self.logger.info(f"  修饰符: {method_info['modifiers']}")
        
        return methods, method_line_map

    def find_method_end_line(self, method_node):
        """
        估算方法的结束行号。

        Args:
            method_node (dict): 方法节点的AST字典

        Returns:
            int: 方法的结束行号
        """
        max_line = 0
        
        def find_max_line(node):
            nonlocal max_line
            if not isinstance(node, (dict, list)):
                return
                
            if isinstance(node, dict):
                pos = node.get('position', {})
                if pos:
                    line = pos.get('line', 0)
                    max_line = max(max_line, line)
                for value in node.values():
                    find_max_line(value)
            elif isinstance(node, list):
                for item in node:
                    find_max_line(item)

        find_max_line(method_node)
        return max_line

    def get_method_modifiers(self, node):
        """
        获取方法的修饰符和注解。

        Args:
            node (dict): 方法节点的AST字典

        Returns:
            list: 修饰符和注解的列表，如 ['public', 'static', '@Override']
        """
        modifiers = []
        if 'modifiers' in node:
            for modifier in node['modifiers']:
                if isinstance(modifier, dict):
                    modifier_type = modifier.get('type')
                    if modifier_type == 'Modifier':
                        modifiers.append(modifier.get('value'))
                    elif modifier_type == 'Annotation':
                        # 处理注解
                        annotation_name = modifier.get('name', {}).get('value')
                        if annotation_name:
                            modifiers.append(f'@{annotation_name}')
        return modifiers

    def analyze_method_calls(self, ast, methods, file_path):
        """
        分析方法的调用关系。

        Args:
            ast (dict): AST节点
            methods (list): 要分析的方法信息列表
            file_path (str): 文件路径

        Returns:
            dict: 方法调用关系
        """
        calls = {
            'callers': {},
            'callees': {}
        }
        
        # 初始化每个方法的调用信息
        for method_info in methods:
            method_name = method_info['name']
            base_info = {
                'file_path': file_path,
                'method_code': self.get_method_code(file_path, method_info['start_line'], method_info['end_line']),
                'start_line': method_info['start_line'],
                'end_line': method_info['end_line'],
                'type': method_info['type'],
                'modifiers': method_info['modifiers']
            }
            calls['callers'][method_name] = {**base_info, 'callers': []}
            calls['callees'][method_name] = {**base_info, 'callees': []}

        def traverse_for_calls(node, current_method=None):
            """
             分析AST树，查找方法调用关系。

             Args:
                 node (dict): AST节点
                 current_method (str): 当前方法的名称
            """
            if not isinstance(node, (dict, list)):
                return
                
            if isinstance(node, dict):
                node_type = node.get('type')
                
                if node_type in ['MethodDeclaration', 'ConstructorDeclaration']:
                    current_method = node.get('name', '<anonymous>')
                    self.logger.debug(f"进入方法: {current_method}")
                
                elif node_type == 'MethodInvocation':
                    called_method = node.get('member')
                    qualifier = node.get('qualifier', '')
                    pos = node.get('position', {})
                    line_num = pos.get('line', 0) if pos else 0
                    
                    if current_method and called_method:
                        self.logger.debug(
                            f"发现方法调用: {qualifier}.{called_method} "
                            f"在方法 {current_method} 的第 {line_num} 行"
                        )
                        
                        call_info = {
                            'name': called_method,
                            'line': line_num,
                            'type': 'method',
                            'qualifier': qualifier
                        }

                        if current_method in calls['callees']:
                            calls['callees'][current_method]['callees'].append(call_info)
                        if called_method in calls['callers']:
                            caller_info = {
                                'name': current_method,
                                'line': line_num,
                                'type': 'method',
                                'qualifier': qualifier
                            }
                            calls['callers'][called_method]['callers'].append(caller_info)

                # 递归遍历子节点
                for value in node.values():
                    traverse_for_calls(value, current_method)
            elif isinstance(node, list):
                for item in node:
                    traverse_for_calls(item, current_method)

        # 只在调试级别记录详细信息
        self.logger.debug("\n开始分析方法调用关系:")
        traverse_for_calls(ast)
        
        # 记录分析结果（调试级别）
        self.logger.debug("\n方法调用关系汇总:")
        for method_name in calls['callees']:
            self.logger.debug(f"\n{method_name}:")
            self.logger.debug("  调用了:")
            for callee in calls['callees'][method_name]['callees']:
                self.logger.debug(
                    f"    - {callee['name']} "
                    f"(行 {callee['line']}, 类型: {callee['type']}, "
                    f"qualifier: {callee['qualifier']})"
                )
            self.logger.debug("  被调用:")
            for caller in calls['callers'][method_name]['callers']:
                self.logger.debug(
                    f"    - 被 {caller['name']} 调用 "
                    f"(行 {caller['line']}, 类型: {caller['type']}, "
                    f"qualifier: {caller['qualifier']})"
                )
        
        return calls

    def get_method_code(self, file_path, start_line, end_line):
        """
        获取指定行范围内的源代码。

        Args:
            file_path (str): 文件路径
            start_line (int): 开始行号
            end_line (int): 结束行号

        Returns:
            str: 指定范围内的源代码文本
        """
        try:
            # 构建完整的文件路径
            full_path = os.path.join(self.src_root, file_path)
            self.logger.debug("读取文件: %s", full_path)
            
            if not os.path.exists(full_path):
                self.logger.error("文件不存在: %s", full_path)
                return ""

            with open(full_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                return ''.join(lines[start_line-1:end_line])
        except Exception as e:
            self.logger.error("读取方法源码时出错: %s", e)
            return ""

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

class JavaChangeAnalyzer:
    """Java代码修改分析器，用于分析多个Java文件的修改"""

    def __init__(self, output_dir="analysis_results"):
        """
        初始化分析器。

        Args:
            output_dir (str): 分析结果输出目录
        """
        self.output_dir = output_dir
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        # 配置日志
        self.logger = self._setup_logger()
        # 创建 AST 提取器并共享日志配置
        self.ast_extractor = JavaASTExtractor(self.logger)

    def _setup_logger(self):
        """配置日志记录器"""
        # 创建logger
        logger = logging.getLogger('JavaAnalyzer')  # 使用统一的logger名称
        logger.setLevel(logging.INFO)

        # 创建日志文件处理器
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(self.output_dir, f'java_analysis_{timestamp}.log')
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)

        # 创建控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # 创建格式化器
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # 添加处理器到logger
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        return logger

    def analyze_diff(self, diff_text):
        """分析git diff文本中的所有文件修改。"""
        try:
            # 解析diff获取修改信息
            changes = self.ast_extractor.parse_diff(diff_text)
            if not changes:
                self.logger.info("没有找到需要分析的文件修改")
                return None
            
            # 分析所有修改的文件
            analysis_results = {}
            self.logger.info("\n分析所有修改的文件:")
            
            for file_path, info in changes.items():
                self.logger.info(f"\n\n===== 分析文件: {file_path} =====")
                self.logger.info(f"修改的行号: {info['modified_lines']}")
                
                try:
                    # 分析单个文件
                    result = self.ast_extractor.analyze_file(file_path, info['modified_lines'])
                    if result:
                        analysis_results[file_path] = result
                        self.logger.info("\n分析结果:")
                        self.logger.info(json.dumps(result, indent=2, ensure_ascii=False))
                    else:
                        self.logger.info(f"无法分析文件: {file_path}")
                except Exception as e:
                    self.logger.error(f"分析文件 {file_path} 时出错: {e}")
                    continue
            
            # 保存分析结果
            if analysis_results:
                return self._save_analysis_results(analysis_results, len(changes))
            else:
                self.logger.info("没有成功分析任何文件")
                return None
            
        except Exception as e:
            self.logger.error(f"分析过程出错: {e}")
            return None

    def _save_analysis_results(self, analysis_results, total_files):
        """
        保存分析结果到文件。

        Args:
            analysis_results (dict): 分析结果
            total_files (int): 分析的文件总数

        Returns:
            str: 保存的文件路径
        """
        try:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = os.path.join(
                self.output_dir,
                f"analysis_all_files_{timestamp}.json"
            )
            
            result_data = {
                "metadata": {
                    "analysis_time": timestamp,
                    "total_files": total_files
                },
                "file_analyses": analysis_results
            }
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(result_data, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"\n所有分析结果已保存到: {output_file}")
            return output_file
            
        except Exception as e:
            self.logger.error(f"保存分析结果时出错: {e}")
            return None

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='Java代码修改分析工具')
    parser.add_argument('--debug', action='store_true', help='启用调试模式')
    parser.add_argument('--src-dir', type=str, required=True, 
                       help='Java源代码根目录路径，例如: /path/to/project/src')
    parser.add_argument('--output-dir', type=str, default='analysis_results',
                       help='分析结果输出目录路径 (默认: analysis_results)')
    args = parser.parse_args()

    # 检查源代码目录是否存在
    if not os.path.exists(args.src_dir):
        print(f"错误: 源代码目录不存在: {args.src_dir}")
        return

    # 设置日志级别
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.getLogger('JavaAnalyzer.AST').setLevel(log_level)

    # diff文本示例
    diff_text = """diff --git src://src/main/java/net/coobird/thumbnailator/filters/Colorize.java dst://src/main/java/net/coobird/thumbnailator/filters/Colorize.java
index d766fa5..34cd875 100644
--- src://src/main/java/net/coobird/thumbnailator/filters/Colorize.java
+++ dst://src/main/java/net/coobird/thumbnailator/filters/Colorize.java
@@ -85,31 +85,33 @@ public final class Colorize implements ImageFilter {
 					"Specified alpha value is outside the range of allowed " +
 					"values.");
 		}
 		
 		int r = c.getRed();
 		int g = c.getGreen();
 		int b = c.getBlue();
 		int a = alpha;
 		
 		this.c = new Color(r, g, b, a);
+		System.out.println("Colorize: " + this.c);
 	}
 	
 	public BufferedImage apply(BufferedImage img) {
 		int width = img.getWidth();
 		int height = img.getHeight();
 		
 		BufferedImage newImage = new BufferedImageBuilder(width, height).build();
 		
 		Graphics2D g = newImage.createGraphics();
 		g.drawImage(img, 0, 0, null);
 		g.setColor(c);
 		g.fillRect(0, 0, width, height);
 		g.dispose();
 
 		if (img.getType() != newImage.getType()) {
 			return BufferedImages.copy(newImage, img.getType());
 		}
-
+		System.out.println("Colorize: " + newImage);
 		return newImage;
 	}
 }
+
diff --git src://src/main/java/net/coobird/thumbnailator/filters/Flip.java dst://src/main/java/net/coobird/thumbnailator/filters/Flip.java
index 98d5432..eaed0f5 100644
--- src://src/main/java/net/coobird/thumbnailator/filters/Flip.java
+++ dst://src/main/java/net/coobird/thumbnailator/filters/Flip.java
@@ -44,21 +44,21 @@ public class Flip {
 		public BufferedImage apply(BufferedImage img) {
 			int width = img.getWidth();
 			int height = img.getHeight();
 			
 			BufferedImage newImage =
 					new BufferedImageBuilder(width, height, img.getType()).build();
 			
 			Graphics g = newImage.getGraphics();
 			g.drawImage(img, width, 0, 0, height, 0, 0, width, height, null);
 			g.dispose();
-			
+            System.err.println("Flip.HORIZONTAL.apply(BufferedImage img) called");
 			return newImage;
 		}
 	};
 	
 	/**
 	 * An image filter which performs a vertical flip of the image.
 	 */
 	public static final ImageFilter VERTICAL = new ImageFilter() {
 		public BufferedImage apply(BufferedImage img) {
 			int width = img.getWidth();
    """
    
    try:
        # 创建分析器并运行分析
        analyzer = JavaChangeAnalyzer(output_dir=args.output_dir)
        # 设置源代码根目录
        analyzer.ast_extractor.src_root = args.src_dir
        result_file = analyzer.analyze_diff(diff_text)
        
        if result_file:
            print(f"分析完成，结果保存在: {result_file}")
        else:
            print("分析失败")
    except Exception as e:
        print(f"执行分析时出错: {e}")

if __name__ == "__main__":
    main()