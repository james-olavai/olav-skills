"""Display-only context for immutable public legacy documentation."""


def on_page_markdown(markdown, page, config, files):
    if page.file.src_uri.startswith('legacy/'):
        page.meta.setdefault('search', {})['exclude'] = True
        chinese = page.file.src_uri.endswith('.zh.md')
        if chinese:
            banner = ('!!! warning "旧平台历史文档"\n'
                      '    以下原文属于已停止维护的旧 OLAV 平台，不是 Olav Skills 0.1.1 的功能或安装说明。'
                      '请使用[当前手册](/zh/)及[迁移指南](/zh/migration/)。原命令、版本和声明按历史保留。\n\n')
        else:
            banner = ('!!! warning "Legacy platform archive"\n'
                      '    This original page describes the retired OLAV platform, not Olav Skills 0.1.1. '
                      'Use the [current manual](/) and [migration guide](/migration/). '
                      'Original commands, versions and claims are preserved as historical material.\n\n')
        return banner + markdown
    return markdown
