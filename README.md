# AST Extraction

一个基于 Python 的 Java 代码分析工具，用于分析 Java 项目中的代码修改和方法调用关系。

## 功能特点

- 分析 Git diff 中的代码修改
- 构建方法调用关系图
- 识别受影响的方法和依赖关系
- 支持方法级别的源代码追踪
- 生成详细的分析报告

## 主要组件

### JavaChangeAnalyzer (java_analyzer.py)

Java 代码修改分析器的主要入口类，负责：
- 分析 Git diff 文本
- 协调整体分析流程
- 生成分析报告
- 配置日志记录

### CallGraph (call_graph.py)

方法调用关系图的核心实现，提供：
- 构建方法调用关系
- 存储方法节点信息
- 分析调用者和被调用者关系
- 序列化调用图数据

### JavaASTExtractor (ast_extractor.py)

Java 代码的 AST 分析器，功能包括：
- 解析 Java 源代码
- 提取方法信息
- 分析方法调用
- 处理类型解析和导入关系

## 使用方法

1. 安装依赖：
```bash
pip install javalang
```

2. 运行分析器：
```bash
python java_analyzer.py --src-dir /path/to/java/project --output-dir analysis_results [--debug]
```

参数说明：
- `--src-dir`: Java 源代码根目录路径（必需）
- `--output-dir`: 分析结果输出目录（可选，默认为 analysis_results）
- `--debug`: 启用调试模式，输出详细日志（可选）

## 输出结果

分析器会生成以下文件：
- 分析日志文件（`java_analysis_YYYYMMDD_HHMMSS.log`）
- 调用图数据（`call_graph.json`）
- 文件分析结果（`analysis_all_files_YYYYMMDD_HHMMSS.json`）

## 注意事项

- 确保 Java 源代码目录结构完整
- 建议在分析大型项目时使用 `--debug` 模式追踪详细信息
- 分析结果会包含方法的源代码，请注意信息安全

## 技术依赖

- Python 3.6+
- javalang 解析器
- 标准库：json, logging, os, datetime, re 等

