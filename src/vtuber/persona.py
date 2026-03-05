"""Persona system - customizable personality for the digital life agent."""

from dataclasses import dataclass, field
from pathlib import Path
import re


@dataclass
class Persona:
    """Defines the agent's personality and behavior."""

    name: str = "VTuber"
    description: str = "A friendly digital life companion."
    traits: list[str] = field(default_factory=lambda: ["friendly", "curious", "helpful"])
    speaking_style: str = "casual and warm"
    language: str = "zh-CN"

    @classmethod
    def from_markdown(cls, path: Path) -> "Persona":
        """Load persona from markdown file."""
        if not path.exists():
            return cls()

        content = path.read_text(encoding="utf-8")

        # Parse basic info
        name_match = re.search(r"- Name:\s*(.+)", content)
        desc_match = re.search(r"- Description:\s*(.+)", content)

        # Parse traits
        traits = []
        traits_match = re.search(r"## Personality Traits\n(.*?)(?=\n##|$)", content, re.DOTALL)
        if traits_match:
            traits = re.findall(r"-\s*(.+)", traits_match.group(1))

        # Parse speaking style
        style = "casual and warm"
        style_match = re.search(r"## Speaking Style\n(.*?)(?=\n##|$)", content, re.DOTALL)
        if style_match:
            styles = re.findall(r"-\s*(.+)", style_match.group(1))
            if styles:
                style = " and ".join(styles)

        return cls(
            name=name_match.group(1).strip() if name_match else "VTuber",
            description=desc_match.group(1).strip() if desc_match else "",
            traits=traits if traits else ["friendly", "curious", "helpful"],
            speaking_style=style,
        )

    def to_system_prompt(self) -> str:
        traits_str = "、".join(self.traits)
        return (
            f"你是 {self.name}，{self.description}\n\n"
            f"## 性格特点\n{traits_str}\n\n"
            f"## 说话风格\n{self.speaking_style}\n\n"
            f"## 语言\n使用 {self.language} 进行交流。\n\n"
            f"## 内置能力\n"
            f"你拥有以下工具：\n"
            f"- **记忆** (memorize/recall/forget): 你可以记住和回忆跨对话的持久记忆\n"
            f"- **日程** (schedule_create/schedule_list/schedule_cancel): 你可以创建定时提醒\n"
            f"- **心跳** (heartbeat): 你可以记录你的活动状态\n\n"
            f"请自然地使用这些工具来增强你的交互体验。"
        )
