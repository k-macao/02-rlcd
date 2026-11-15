# 行情面板字体来源

本目录的行情数据面板渲染统一使用 **阿里巴巴普惠体 3.0 / Alibaba PuHuiTi 3.0，Version 3.01**：

- `AlibabaPuHuiTi-3-55-Regular.ttf`：指数名称、列标题、单位等文字。
- `AlibabaPuHuiTi-3-85-Bold.ttf`：数值串、顶栏标题和市场色块。

字体文件保持原样，渲染器不会回退到其他字体家族。固件中的小型 LVGL 字库由上述文件按界面实际字符集转换生成（`RLCD_CLOCK/tools/gen_market_panel_fonts.py`）。

## 来源固定版本

- Regular：<https://github.com/shelwinjue/alibabapuhuiti-3-55-regular/tree/d738f72e8e33718849f3533adf59cb98cf047aca>
- Bold：<https://github.com/shelwinjue/alibabapuhuiti-3-85-bold/tree/7c5d25f98c0c6bff76400c1d8d2e2aa0720c7b8d>

字体名称表中的版权信息：

> Copyright (c) 2020-2023 Alibaba (China) Co., Ltd. All rights reserved.
>
> (c) 2020-2023 阿里巴巴（中国）有限公司，版权所有。

阿里巴巴普惠体的字体版权与使用条款归其权利人所有；此处仅记录本项目实际使用的文件和来源，不改写其授权条款。
