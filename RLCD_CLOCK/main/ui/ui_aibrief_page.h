// 声明行情数据面板页（固件常驻主页）的构建、刷新与对象清理接口。
// 文件名沿用旧的 aibrief：这一页占用的仍是原 AI 简报页的页面槽位与位图通道。
#pragma once

bool build_aibrief_page();
// 行情或页脚状态有变化并已重画时返回 true，调用方随后刷新屏幕。
bool update_aibrief_page();
void clear_aibrief_page_refs();
