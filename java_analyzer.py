# -*- coding: utf-8 -*-
import json
import logging
import os
import datetime
import argparse
import sys
from ast_extractor import JavaASTExtractor

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
        logger = logging.getLogger('JavaAnalyzer')
        logger.setLevel(logging.DEBUG)
        
        # 创建日志文件处理器
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(self.output_dir, f'java_analysis_{timestamp}.log')
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)  # 文件始终记录DEBUG级别
        
        # 创建控制台处理器
        console_handler = logging.StreamHandler()
        # 根据命令行参数设置控制台日志级别
        console_level = logging.DEBUG if '--debug' in sys.argv else logging.INFO
        console_handler.setLevel(console_level)
        
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
                        self.logger.info(f"{result}")
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
    logging.getLogger('JavaAnalyzer').setLevel(log_level)
    
#     diff_text = """diff --git src://src/main/java/quick/pager/shop/handler/AbstractHandler.java dst://src/main/java/quick/pager/shop/handler/AbstractHandler.java
# index d766fa5..34cd875 100644
# --- src://src/main/java/quick/pager/shop/handler/AbstractHandler.java
# +++ dst://src/main/java/quick/pager/shop/handler/AbstractHandler.java
# @@ -26,7 +26,7 @@ public abstract class AbstractHandler implements IHandler {
#          jobLog.setExecutorServiceName(jobInfo.getServiceName());
#          jobLog.setExecutorServiceMethod(jobInfo.getServiceMethod());
#          jobLog.setExecutorParam(executorsParam);
# -        jobLog.setHandleTime(LocalDateTime.now());
# +        System.out.println("Pre log handle time: " + LocalDateTime.now());
#          jobLogMapper.insert(jobLog);
#          return jobLog.getId();
#      }
# @@ -36,7 +36,7 @@ public abstract class AbstractHandler implements IHandler {
#          JobLogMapper jobLogMapper = ShopSpringContext.getBean(JobLogMapper.class);
#          JobLog updateJobLog = new JobLog();
#          updateJobLog.setId(jobLogId);
# -        updateJobLog.setHandleStatus(1);
# +        System.out.println("Post log handle status: " + 1);
#          updateJobLog.setHandleTime(LocalDateTime.now());
#          jobLogMapper.updateById(updateJobLog);
#      }
#      """

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
+     System.out.println("Colorize: " + this.c);
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
+     System.out.println("Colorize: " + newImage);
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
    """


    # diff文本示例
#     diff_text = """diff --git src://src/main/java/com/example/service/UserService.java dst://src/main/java/com/example/service/UserService.java
# index a123456..b789012 100644
# --- src://src/main/java/com/example/service/UserService.java
# +++ dst://src/main/java/com/example/service/UserService.java
# @@ -12,6 +12,7 @@ public class UserService {
#      public User createUser(String name, String email) {
#          logUtil.info("Creating new user: " + name);
# +        logUtil.debug("Validating email: " + email);
         
#          if (!validationUtil.validateEmail(email)) {
#              logUtil.error("Invalid email: " + email);
# @@ -25,6 +26,7 @@ public class UserService {
#      public void updateUser(Long id, String name) {
#          User user = userRepository.findById(id);
# +        validationUtil.validateName(name);
#          user.setName(name);
#          userRepository.save(user);
#          logUtil.info("User updated: " + id);
# diff --git src://src/main/java/com/example/util/ValidationUtil.java dst://src/main/java/com/example/util/ValidationUtil.java
# index 1234567..89abcdef 100644
# --- src://src/main/java/com/example/util/ValidationUtil.java
# +++ dst://src/main/java/com/example/util/ValidationUtil.java
# @@ -3,4 +3,5 @@ public class ValidationUtil {
#      public boolean validateEmail(String email) {
# +        System.err.println("Validating email: " + email);
#          return email != null && email.contains("@");
#      }
#  }"""
    
    try:
        # 创建分析器并运行分析
        analyzer = JavaChangeAnalyzer(output_dir=args.output_dir)
        analyzer.ast_extractor.src_root = args.src_dir
        # 首先建立项目索引
        analyzer.ast_extractor.build_project_index()
        # 然后分析diff
        result_file = analyzer.analyze_diff(diff_text)
        
        if result_file:
            print(f"分析完成，结果保存在: {result_file}")
        else:
            print("分析失败，请查看日志文件了解详细信息。")
            
    except Exception as e:
        print(f"运行时出错: {e}")
        return

if __name__ == '__main__':
    main() 