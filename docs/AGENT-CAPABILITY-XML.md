# Agent capability XML for the shell copy

Paste these blocks, in this order, before the `<signal name="Event">` block in `shani-shell/bridge/agent_contract.py`. Every method returns one JSON string. Generated from `shani_chronoa.agent_capabilities`, the same table the TUI and CLI read.

```xml
    <method name="SettingsList">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SettingsGet">
      <arg name="name" type="s" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SettingsSet">
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SettingsPreset">
      <arg name="name" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SettingsEmail">
      <arg name="values_json" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="ModelsList">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="ModelsUse">
      <arg name="name" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SetupState">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SetupStep">
      <arg name="step" type="s" direction="in"/>
      <arg name="value" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="Conversations">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="ConversationsResume">
      <arg name="ref" type="s" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="ConversationsRename">
      <arg name="ref" type="s" direction="in"/>
      <arg name="title" type="s" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="ConversationsDelete">
      <arg name="ref" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="MemoryList">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="MemoryDecide">
      <arg name="id" type="s" direction="in"/>
      <arg name="decision" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="Routines">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="RoutinesRun">
      <arg name="name" type="s" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="Triggers">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="Notices">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="NoticesDismiss">
      <arg name="id" type="s" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="VoiceEngines">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="Activity">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="DiagnosticsProbe">
      <arg name="json" type="s" direction="out"/>
    </method>
```
