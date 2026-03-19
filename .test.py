#!/usr/bin/env python3
"""
统计项目中所有 Python 文件的代码行数
包含：总行数、代码行数、注释行数、空行数
"""

import os
import sys
from pathlib import Path
from collections import defaultdict


def count_lines(file_path):
    """
    统计单个文件的行数
    返回: (总行数, 代码行数, 注释行数, 空行数)
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"无法读取文件 {file_path}: {e}")
        return 0, 0, 0, 0

    total = len(lines)
    code = 0
    comment = 0
    blank = 0
    in_string = False

    for line in lines:
        stripped = line.strip()

        # 空行
        if not stripped:
            blank += 1
            continue

        # 注释行
        if stripped.startswith('#'):
            comment += 1
            continue

        # 代码行（包含可能的注释）
        code += 1

    return total, code, comment, blank


def count_project_lines(root_dir='.', extensions=['.py'], exclude_dirs=None):
    """
    统计整个项目的代码行数

    Args:
        root_dir: 项目根目录
        extensions: 要统计的文件扩展名列表
        exclude_dirs: 要排除的目录名称列表
    """
    if exclude_dirs is None:
        exclude_dirs = {'.git', '__pycache__', '.venv', 'venv', 'env', '.idea', '.vscode', 'node_modules',
                        '.pytest_cache', 'dist', 'build', '*.egg-info'}

    stats = defaultdict(lambda: {'total': 0, 'code': 0, 'comment': 0, 'blank': 0, 'files': 0})
    file_details = []

    root_path = Path(root_dir)

    for py_file in root_path.rglob('*'):
        # 检查是否为文件
        if not py_file.is_file():
            continue

        # 检查文件扩展名
        if py_file.suffix not in extensions:
            continue

        # 检查是否在排除目录中
        if any(excluded in py_file.parts for excluded in exclude_dirs):
            continue

        # 统计行数
        total, code, comment, blank = count_lines(py_file)

        ext = py_file.suffix
        stats[ext]['total'] += total
        stats[ext]['code'] += code
        stats[ext]['comment'] += comment
        stats[ext]['blank'] += blank
        stats[ext]['files'] += 1

        file_details.append({
            'file': str(py_file),
            'total': total,
            'code': code,
            'comment': comment,
            'blank': blank
        })

    return stats, file_details


def print_report(stats, file_details, show_details=False):
    """打印统计报告"""

    print("\n" + "=" * 70)
    print("📊 代码行数统计报告")
    print("=" * 70)

    total_all = sum(s['total'] for s in stats.values())
    code_all = sum(s['code'] for s in stats.values())
    comment_all = sum(s['comment'] for s in stats.values())
    blank_all = sum(s['blank'] for s in stats.values())
    files_all = sum(s['files'] for s in stats.values())

    print(f"\n📈 总体统计:")
    print(f"  • 文件总数:   {files_all:,} 个")
    print(f"  • 总行数:     {total_all:,} 行")
    print(f"  • 代码行数:   {code_all:,} 行")
    print(f"  • 注释行数:   {comment_all:,} 行")
    print(f"  • 空白行数:   {blank_all:,} 行")

    if total_all > 0:
        print(f"\n📊 代码占比:")
        print(f"  • 代码: {code_all / total_all * 100:5.2f}%")
        print(f"  • 注释: {comment_all / total_all * 100:5.2f}%")
        print(f"  • 空行: {blank_all / total_all * 100:5.2f}%")

    if len(stats) > 1:
        print(f"\n📝 按文件类型统计:")
        print(f"{'文件类型':<15} {'文件数':<10} {'代码行数':<15} {'占比':<10}")
        print("-" * 50)
        for ext in sorted(stats.keys()):
            s = stats[ext]
            pct = s['code'] / code_all * 100 if code_all > 0 else 0
            print(f"{ext:<15} {s['files']:<10} {s['code']:<15,} {pct:>6.2f}%")

    if show_details and file_details:
        print(f"\n📄 单个文件详情 (前 20 个):")
        print(f"{'文件路径':<50} {'代码行数':<12}")
        print("-" * 65)
        for detail in sorted(file_details, key=lambda x: x['code'], reverse=True)[:20]:
            filename = detail['file']
            if len(filename) > 50:
                filename = "..." + filename[-47:]
            print(f"{filename:<50} {detail['code']:<12,}")

    print("\n" + "=" * 70 + "\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='统计项目代码行数')
    parser.add_argument('path', nargs='?', default='.', help='项目路径 (默认: 当前目录)')
    parser.add_argument('-d', '--details', action='store_true', help='显示单个文件详情')
    parser.add_argument('-e', '--ext', default='.py', help='文件扩展名 (默认: .py)')

    args = parser.parse_args()

    # 解析多个扩展名
    extensions = [ext if ext.startswith('.') else '.' + ext for ext in args.ext.split(',')]

    print(f"📂 正在扫描: {args.path}")
    stats, file_details = count_project_lines(args.path, extensions)

    if not file_details:
        print(f"❌ 未找到 {extensions} 文件")
        sys.exit(1)

    print_report(stats, file_details, args.details)


if __name__ == '__main__':
    main()