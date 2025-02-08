# -*- coding: utf-8 -*-
import argparse
import logging
import os
from ast_extractor import JavaASTExtractor

def setup_logger():
    """配置日志记录器"""
    logger = logging.getLogger('CallGraphGenerator')
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

def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='生成Java项目的函数调用图')
    parser.add_argument('--src-dir', type=str, required=True, 
                       help='Java源代码根目录路径，例如: /path/to/project/src')
    parser.add_argument('--output-dir', type=str, default='analysis_results',
                       help='输出目录路径 (默认: analysis_results)')
    parser.add_argument('--debug', action='store_true', 
                       help='启用调试模式')
    args = parser.parse_args()

    # 设置日志
    logger = setup_logger()
    if args.debug:
        logger.setLevel(logging.DEBUG)
        for handler in logger.handlers:
            handler.setLevel(logging.DEBUG)
    
    logger.info(f"开始分析项目: {args.src_dir}")

    try:
        # 创建AST提取器并传递日志配置
        ast_extractor = JavaASTExtractor(logger)
        ast_extractor.debug_mode = args.debug
        
        # 分析项目并构建调用图
        logger.info("开始分析项目...")
        call_graph = ast_extractor.analyze_project(args.src_dir)
        
        if not call_graph:
            logger.error("调用图构建失败")
            return 1
            
        # 输出一些基本统计信息
        logger.info(f"找到的方法总数: {len(call_graph.nodes)}")
        logger.info(f"method_index中的方法总数: {len(ast_extractor.method_index)}")
        
        # 检查method_index和调用图的一致性
        missing_methods = set(call_graph.nodes.keys()) - set(ast_extractor.method_index.keys())
        if missing_methods:
            logger.warning(f"发现 {len(missing_methods)} 个方法不在method_index中:")
            for method in sorted(missing_methods)[:10]:  # 只显示前10个
                logger.warning(f"  - {method}")
                
        # 检查调用关系
        total_calls = sum(len(edges['callees']) for edges in call_graph.edges.values())
        logger.info(f"找到的调用关系总数: {total_calls}")
        
        if total_calls == 0:
            logger.warning("没有找到任何调用关系！")
            # 输出一些调试信息
            logger.debug("检查前10个方法的信息:")
            for method_name in list(call_graph.nodes.keys())[:10]:
                logger.debug(f"方法: {method_name}")
                logger.debug(f"  - 信息: {call_graph.nodes[method_name]}")
                if method_name in call_graph.edges:
                    logger.debug(f"  - 调用者: {call_graph.edges[method_name]['callers']}")
                    logger.debug(f"  - 被调用: {call_graph.edges[method_name]['callees']}")
        
        # 保存调用图到文件
        logger.info("保存调用图...")
        output_file = os.path.join(args.output_dir, 'call_graph.json')
        call_graph.save(output_file)
        
        # 输出统计信息
        logger.info("获取调用图统计信息...")
        stats = call_graph.get_stats()
        
        if stats:
            logger.info("调用图统计信息:")
            for key, value in stats.items():
                logger.info(f"  {key}: {value}")
        
        logger.info("调用图构建完成")

    except Exception as e:
        logger.error(f"生成调用图时出错: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return 1

    return 0

if __name__ == '__main__':
    exit(main()) 