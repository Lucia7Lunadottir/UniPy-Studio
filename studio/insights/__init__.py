"""Подсказки в коде: семантическая подсветка, подсказки в строке (inlay hints), CodeLens.

Реестр INSIGHTS — новая возможность = класс-наследник base.Insight в своём модуле + строка здесь
(+ вид запроса в lsp_features.FEATURES для языкового сервера и/или метод _<вид> у intellisense.JediWorker).
Каждому основному редактору — свой InsightController (editor.insights); включение — настройки insights/*.
"""
from .base import Insight, InsightController  # noqa: F401
from .codelens import CodeLens
from .inlay import InlayHints
from .semantic import SemanticHighlighting

INSIGHTS = (SemanticHighlighting, InlayHints, CodeLens)


def controller_for(editor):
    return InsightController(editor, INSIGHTS)
