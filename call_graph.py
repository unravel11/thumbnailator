# -*- coding: utf-8 -*-
import json
import datetime
import os
import logging
from concurrent.futures import ProcessPoolExecutor

class CallGraph:
    """表示方法调用关系图的类"""

    def __init__(self):
        self.nodes = {}  # 存储所有方法节点
        self.edges = {}  # 存储调用关系
        self.logger = logging.getLogger('CallGraph')
        self.logger.setLevel(logging.DEBUG)

    def add_method(self, qualified_name, method_info):
        """添加方法节点"""
        try:
            self.logger.debug(f"添加方法: {qualified_name}")
            self.logger.debug(f"方法信息: {method_info}")
            
            # 将 modifiers 集合转换为列表
            modifiers = list(method_info.get('modifiers', set())) if isinstance(method_info.get('modifiers'), set) else method_info.get('modifiers', [])
            
            self.nodes[qualified_name] = {
                'name': method_info['name'],
                'qualified_name': qualified_name,
                'file_path': method_info['file_path'],
                'class_name': method_info['class_name'],
                'start_line': method_info.get('start_line'),
                'end_line': method_info.get('end_line'),
                'type': method_info['type'],
                'modifiers': modifiers,  # 使用转换后的列表
                'signature': method_info.get('signature', '')
            }
            # 确保方法在 edges 中有一个入口
            if qualified_name not in self.edges:
                self.edges[qualified_name] = {
                    'callers': set(),  # 调用此方法的方法
                    'callees': set()   # 此方法调用的方法
                }
            self.logger.debug(f"当前已索引方法数: {len(self.nodes)}")
        except Exception as e:
            self.logger.error(f"添加方法时出错 {qualified_name}: {str(e)}")

    def add_call(self, caller, callee):
        """添加调用关系（双向）
        
        Args:
            caller: 调用方法的完整限定名
            callee: 被调用方法的完整限定名
        """
        try:
            # 参数验证
            if not caller or not callee:
                self.logger.warning(f"跳过无效的调用关系: {caller} -> {callee}")
                return
            
            # 规范化方法名（移除多余的点号）
            caller = caller.strip('.')
            callee = callee.strip('.')
            
            # 验证方法名格式
            if not self._is_valid_method_name(caller) or not self._is_valid_method_name(callee):
                self.logger.warning(f"跳过格式无效的调用关系: {caller} -> {callee}")
                return
            
            self.logger.debug(f"添加调用关系: {caller} -> {callee}")
            
            # 确保两个方法都在图中
            if caller not in self.edges:
                self.edges[caller] = {'callers': set(), 'callees': set()}
            if callee not in self.edges:
                self.edges[callee] = {'callers': set(), 'callees': set()}
            
            # 建立双向关系
            self.edges[caller]['callees'].add(callee)  # caller 调用了 callee
            self.edges[callee]['callers'].add(caller)  # callee 被 caller 调用
            
            self.logger.debug(f"当前调用关系总数: {sum(len(e['callees']) for e in self.edges.values())}")
            
        except Exception as e:
            self.logger.error(f"添加调用关系时出错 ({caller} -> {callee}): {str(e)}")

    def _is_valid_method_name(self, method_name):
        """验证方法名格式是否有效
        
        Args:
            method_name: 方法的完整限定名
            
        Returns:
            bool: 方法名格式是否有效
        """
        try:
            if not method_name:
                return False
            
            # 检查基本格式
            if not isinstance(method_name, str):
                return False
            
            # 移除多余的点号
            method_name = method_name.strip('.')
            
            # 检查是否包含无效字符
            invalid_chars = set('<>(){}[]\\/')
            if any(c in method_name for c in invalid_chars):
                return False
            
            # 检查是否是有效的Java限定名格式
            parts = method_name.split('.')
            if len(parts) < 2:  # 至少应该有包名和方法名
                return False
            
            # 检查每个部分是否是有效的Java标识符
            for part in parts:
                if not part or not part[0].isalpha() and part[0] != '_':
                    return False
                if not all(c.isalnum() or c == '_' for c in part):
                    return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"验证方法名格式时出错 ({method_name}): {str(e)}")
            return False

    def get_callers(self, method_name):
        """获取调用指定方法的所有方法"""
        if method_name in self.edges:
            return list(self.edges[method_name]['callers'])
        return []

    def get_callees(self, method_name):
        """获取指定方法调用的所有方法"""
        if method_name in self.edges:
            return list(self.edges[method_name]['callees'])
        return []

    def save(self, output_file):
        """保存调用图到JSON文件"""
        try:
            self.logger.info("开始保存调用图...")
            self.logger.info(f"总方法数: {len(self.nodes)}")
            self.logger.info(f"总调用关系数: {sum(len(e['callees']) for e in self.edges.values())}")
            
            # 将所有的 set 转换为 list
            serializable_edges = {}
            for method, calls in self.edges.items():
                serializable_edges[method] = {
                    'callers': list(calls['callers']),
                    'callees': list(calls['callees'])
                }

            # 准备要保存的数据
            data = {
                'metadata': {
                    'total_methods': len(self.nodes),
                    'total_calls': sum(len(e['callees']) for e in self.edges.values()),
                    'generated_time': datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                },
                'methods': self.nodes,
                'call_hierarchy': serializable_edges
            }

            # 创建输出目录（如果不存在）
            os.makedirs(os.path.dirname(output_file), exist_ok=True)

            # 保存为JSON
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"调用图已保存到: {output_file}")
        except Exception as e:
            self.logger.error(f"保存调用图时出错: {str(e)}")
            raise  # 重新抛出异常以便调试

    def get_stats(self):
        """获取调用图的统计信息"""
        try:
            stats = {
                'total_methods': len(self.nodes),
                'total_calls': sum(len(e['callees']) for e in self.edges.values()),
                'methods_with_callers': sum(1 for e in self.edges.values() if e['callers']),
                'methods_with_callees': sum(1 for e in self.edges.values() if e['callees']),
            }
            
            self.logger.info("调用图统计信息:")
            for key, value in stats.items():
                self.logger.info(f"  {key}: {value}")
            
            return stats
        except Exception as e:
            self.logger.error(f"获取统计信息时出错: {str(e)}")
            return None

    def load(self, file_path):
        """从文件加载调用图"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        self.nodes = data['methods']
        # 将列表转换回集合
        self.edges = {
            method: {
                'callers': set(edge_data['callers']),
                'callees': set(edge_data['callees'])
            }
            for method, edge_data in data['call_hierarchy'].items()
        }
        self.logger.info(f"调用图已从: {file_path} 加载") 