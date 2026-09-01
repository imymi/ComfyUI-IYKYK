import { app } from "../../scripts/app.js";
import { EXTENSION_VERSION } from "./version.js";

const NODE_DEFAULTS = {
    // 🎴 节点 1: IYKYK 15槽位提示词生成器
    "IYKYKPromptGenerator": {
        "预设模板": "无 (None)",
        "风格配方": "无 (None)",
        "场景大类": "随机 (Random)",
        "剧情主题": "随机 (Random)",
        "景别构图": "自动 (Auto)",
        "拍摄视角": "自动 (Auto)",
        "裸露等级": "随机 (Random)",
        "服装款式": "随机 (Random)",
        "服装状态": "自动联动裸露等级 (Auto Link Nudity)",
        "发型发色": "随机 (Random)",
        "饰品头饰": "无 (None)",
        "妆容细节": "无 (None)",
        "姿势动作": "随机 (Random)",
        "情绪表情": "随机 (Random)",
        "光影预设": "自动 (Auto)",
        "胶片风格": "无 (None)",
        "液体效果": "无 (None)",
        "纹身标记": "无 (None)",
        "道具物件": "无 (None)",
        "角色设定": "无 (None)",
        "真实微瑕": "无 (None)",
        "画质等级": "高清写真 (High)",
        "prompt_seed": -1,
        "control_after_generate": "fixed"
    },

    // 📋 节点 2: IYKYK 模板浏览器
    "IYKYKPresetBrowser": {
        "预设模板": "01_纯欲胶片 (Pure Desire 35mm Film)",
        "风格配方": "无 (None)",
        "画质等级": "高清写真 (High)",
        "prompt_seed": -1,
        "control_after_generate": "fixed"
    },

    // 🧩 节点 3: IYKYK 自定义槽位拼装器
    "IYKYKCustomSlotCombiner": {
        "prompt_seed": -1,
        "control_after_generate": "fixed",
        "场景主题": "",
        "景别视角": "",
        "裸露状态": "",
        "服装款式": "",
        "光影氛围": "",
        "姿势动作": "",
        "表情眼神": "",
        "风格胶片": "",
        "妆容发型": "",
        "微瑕细节": "",
        "纹身标记": "",
        "道具物件": "",
        "角色体液": "",
        "画质修饰": "best quality, masterpiece",
        "自定义追加": ""
    }
};

/**
 * 递归还原节点全部控件至默认初始值
 */
function resetNodeDefaults(node) {
    if (!node || !node.widgets) return;

    const comfyClass = node.comfyClass || "";
    const classDefaults = NODE_DEFAULTS[comfyClass] || {};

    for (const widget of node.widgets) {
        if (!widget || !widget.name) continue;

        let defVal = undefined;
        if (widget.name in classDefaults) {
            defVal = classDefaults[widget.name];
        } else if (widget.options && widget.options.default !== undefined) {
            defVal = widget.options.default;
        } else if (widget.default_value !== undefined) {
            defVal = widget.default_value;
        }

        if (defVal !== undefined) {
            if (widget.options && Array.isArray(widget.options.values)) {
                if (widget.options.values.includes(defVal)) {
                    widget.value = defVal;
                } else if (widget.options.values.length > 0) {
                    widget.value = widget.options.values[0];
                }
            } else {
                widget.value = defVal;
            }

            if (typeof widget.callback === "function") {
                widget.callback(widget.value);
            }
        }
    }

    node.setDirtyCanvas(true, true);
    if (app && app.graph) {
        app.graph.setDirtyCanvas(true, true);
    }
}

app.registerExtension({
    name: "ComfyUI-IYKYK.UIEnhancements",

    async nodeCreated(node) {
        if (!node || !node.comfyClass || !node.comfyClass.startsWith("IYKYK")) {
            return;
        }

        // 1. 在节点控件区添加「一键还原默认选项」按钮
        const resetBtn = node.addWidget("button", "🔄 恢复默认选项 (Reset Defaults)", null, () => {
            resetNodeDefaults(node);
        });
        resetBtn.serialize = false;

        // 2. 在插件节点右下角绘制半透明版本号水印
        const origOnDrawForeground = node.onDrawForeground;
        node.onDrawForeground = function (ctx) {
            if (origOnDrawForeground) {
                origOnDrawForeground.apply(this, arguments);
            }

            // 折叠状态下不绘制水印
            if (this.flags && this.flags.collapsed) {
                return;
            }

            ctx.save();
            ctx.font = "9px system-ui, -apple-system, sans-serif";
            ctx.fillStyle = "rgba(255, 255, 255, 0.3)";
            ctx.textAlign = "right";
            ctx.textBaseline = "bottom";
            ctx.fillText(EXTENSION_VERSION, this.size[0] - 8, this.size[1] - 5);
            ctx.restore();
        };
    },

    getNodeMenuItems(node) {
        if (!node || !node.comfyClass || !node.comfyClass.startsWith("IYKYK")) {
            return [];
        }

        return [{
            content: "🔄 恢复全部默认选项 (Reset Defaults)",
            callback: () => resetNodeDefaults(node),
        }];
    }
});
