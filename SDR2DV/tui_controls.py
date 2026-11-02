"""Small, accessible Questionary controls. No conversion logic lives here."""
from dataclasses import dataclass
import math
import shutil

import questionary
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.utils import get_cwidth

BACK = '__back__'
STYLE = questionary.Style([
    ('qmark', 'fg:#69c9b5'), ('pointer', 'fg:#69c9b5 bold'),
    ('highlighted', 'fg:#69c9b5 bold'), ('answer', 'fg:#c9dbec'),
    ('option', 'bold'), ('help', 'fg:#8b929d nobold'),
    ('instruction', 'fg:#8b929d'), ('disabled', 'fg:#8b929d nobold'),
])


@dataclass(frozen=True)
class Option:
    value: object
    label: str
    detail: str
    disabled: str | None = None


def wrap_cells(text, width):
    """Wrap Chinese by terminal cells, not Python character count."""
    lines = []; line = ''; cells = 0
    for char in text:
        size = max(0, get_cwidth(char))
        if char == '\n' or (line and cells + size > width):
            lines.append(line); line = ''; cells = 0
        if char != '\n':
            line += char; cells += size
    if line: lines.append(line)
    return lines


def choice_for(option, width):
    detail = option.detail
    if option.disabled: detail += ' 当前不可用：' + option.disabled
    tokens = [('class:disabled' if option.disabled else 'class:option', option.label)]
    for line in wrap_cells(detail, max(12, width - 7)):
        tokens.append(('class:help', '\n     ' + line))
    # The explanation already includes the disabled reason; do not append it twice.
    return questionary.Choice(tokens, value=option.value, disabled=True if option.disabled else None)


def menu_question(title, options, default=None, back=True, width=None, **kwargs):
    options = list(options)
    if back: options.append(Option(BACK, '返回上一级', '保留已确认的设置；也可以按 Esc 返回。'))
    selectable = [option.value for option in options if not option.disabled]
    if not selectable: raise ValueError('此菜单没有可用选项')
    if default not in selectable: default = selectable[0]
    width = width or shutil.get_terminal_size((80, 24)).columns
    question = questionary.select(title, choices=[choice_for(o, width) for o in options],
        default=default, style=STYLE, use_shortcuts=False, erase_when_done=True,
        instruction='↑↓ 选择 · Enter 确认 · Esc ' + ('返回' if back else '退出'), **kwargs)

    @question.application.key_bindings.add('escape')
    def go_back(event): event.app.exit(result=BACK)

    return question


def choose(title, options, default=None, back=True):
    return menu_question(title, options, default, back).unsafe_ask()


def text(title, default='', detail='', validate=None):
    if detail: questionary.print(detail, style='fg:#8b929d')
    bindings = KeyBindings()

    @bindings.add('escape')
    def go_back(event): event.app.exit(result=BACK)

    return questionary.text(title, default=str(default), style=STYLE, validate=validate,
        instruction='Enter 保存 · Esc 放弃本项', key_bindings=bindings).unsafe_ask()


def number_validator(low, high, integer=False):
    def validate(value):
        try:
            parsed = int(value) if integer else float(value)
            if math.isfinite(parsed) and low <= parsed <= high: return True
        except (ValueError, OverflowError): pass
        return f'请输入 {low}～{high} 的' + ('整数' if integer else '数字')
    return validate


def number(title, default, low, high, detail, integer=False):
    value = text(title, default, detail, number_validator(low, high, integer))
    if value == BACK: return BACK
    return int(value) if integer else float(value)


def toggle(title, default, enabled, disabled, labels=('开启', '关闭')):
    return choose(title, [Option(True, labels[0], enabled), Option(False, labels[1], disabled)], default)
