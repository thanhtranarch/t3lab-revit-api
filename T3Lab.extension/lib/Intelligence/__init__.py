# -*- coding: utf-8 -*-
# T3Lab Intelligence Module

try:
    from Intelligence.ai_bridge import ai_bridge, AIModeBridge
except Exception:
    ai_bridge = None
    AIModeBridge = None
