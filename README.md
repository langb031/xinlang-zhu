# 个人公募基金研究终端

## 这是什么
这是一个在自己电脑上运行的公募基金研究页，默认研究博时新兴成长混合（050009）。

## 数据与风险说明
数据通过 AKShare 接入公开网页，接口可能变化；持仓和规模来自定期披露，不是实时数据。本工具不构成投资建议。

## 第一次安装
```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 启动
```bash
.venv/bin/streamlit run app.py
```

## 第一次研究 050009
保留搜索框中的 `050009`，点击“更新当前基金”。

## 更新数据
再次点击“更新当前基金”。失败时页面继续使用上一次成功缓存。

## 常见问题
- `样本不足`：现有数据不足以可靠计算该指标。
- `暂无数据`：公开接口没有返回该项。
- `partial`：核心净值可用，但某项定期披露暂时不可用。

## 运行测试
```bash
.venv/bin/python -m unittest tests.test_core -v
```
