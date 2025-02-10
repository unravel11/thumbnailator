# tests/test_method_index.py
import unittest
import os
import logging
from ast_extractor import JavaASTExtractor

class TestMethodIndex(unittest.TestCase):
    """测试方法索引功能"""

    def setUp(self):
        """测试前的准备工作"""
        # 设置测试项目路径
        self.test_project_path = os.path.join(os.path.dirname(__file__), 'test_project')
        if not os.path.exists(self.test_project_path):
            os.makedirs(self.test_project_path)
            
        # 设置日志记录器
        self.logger = logging.getLogger('TestMethodIndex')
        self.logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(handler)
        
        # 初始化AST提取器
        self.extractor = JavaASTExtractor(logger=self.logger)

    def test_simple_class(self):
        """测试简单类方法的解析"""
        self.extractor.src_root = self.test_project_path
        
        # 创建一个简单类的测试文件
        simple_class = """package com.example;

public class SimpleClass {
    private String name;
    
    public SimpleClass() {
        this.name = "default";
    }
    
    public SimpleClass(String name) {
        this.name = name;
    }
    
    public void method1() {
        // 空方法
    }
    
    private String method2() {
        return "test";
    }
}"""
        
        with open(os.path.join(self.test_project_path, 'SimpleClass.java'), 'w') as f:
            f.write(simple_class)
        
        self.extractor.build_project_index()
        
        # 验证方法索引
        expected_methods = {
            'com.example.SimpleClass.SimpleClass',       # 默认构造函数
            'com.example.SimpleClass.SimpleClass#String', # 带参数构造函数
            'com.example.SimpleClass.method1',           # 空方法
            'com.example.SimpleClass.method2'            # 带返回值的方法
        }
        
        for method in expected_methods:
            self.assertIn(method, self.extractor.method_index, f"方法 {method} 未在索引中找到")
        
        # 验证方法信息
        method1_info = self.extractor.method_index['com.example.SimpleClass.method1']
        self.assertEqual(method1_info['type'], 'method')
        self.assertEqual(method1_info['modifiers'], {'public'})
        
        method2_info = self.extractor.method_index['com.example.SimpleClass.method2']
        self.assertEqual(method2_info['type'], 'method')
        self.assertEqual(method2_info['modifiers'], {'private'})

    def test_abstract_class(self):
        """测试抽象类方法的解析"""
        self.extractor.src_root = self.test_project_path
        
        # 创建一个抽象类的测试文件
        abstract_class = """package com.example;

public abstract class AbstractProcessor {
    protected final String name;
    
    public AbstractProcessor(String name) {
        this.name = name;
    }
    
    public abstract void process();
    
    protected String getName() {
        return name;
    }
    
    public final void init() {
        // 初始化方法
    }
}"""
        
        with open(os.path.join(self.test_project_path, 'AbstractProcessor.java'), 'w') as f:
            f.write(abstract_class)
        
        self.extractor.build_project_index()
        
        # 验证方法索引
        expected_methods = {
            'com.example.AbstractProcessor.AbstractProcessor#String',  # 构造函数
            'com.example.AbstractProcessor.process',           # 抽象方法
            'com.example.AbstractProcessor.getName',           # 具体方法
            'com.example.AbstractProcessor.init'               # final方法
        }
        
        for method in expected_methods:
            self.assertIn(method, self.extractor.method_index, f"方法 {method} 未在索引中找到")
        
        # 验证抽象方法的信息
        process_info = self.extractor.method_index['com.example.AbstractProcessor.process']
        self.assertEqual(process_info['type'], 'abstract_method')
        self.assertEqual(process_info['modifiers'], {'public', 'abstract'})
        
        # 验证具体方法的信息
        get_name_info = self.extractor.method_index['com.example.AbstractProcessor.getName']
        self.assertEqual(get_name_info['type'], 'method')
        self.assertEqual(get_name_info['modifiers'], {'protected'})
        
        # 验证final方法的信息
        init_info = self.extractor.method_index['com.example.AbstractProcessor.init']
        self.assertEqual(init_info['type'], 'method')
        self.assertEqual(init_info['modifiers'], {'public', 'final'})

    def tearDown(self):
        """清理测试文件"""
        import shutil
        if os.path.exists(self.test_project_path):
            shutil.rmtree(self.test_project_path)
        
        # 清理日志处理器
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)

if __name__ == '__main__':
    unittest.main() 