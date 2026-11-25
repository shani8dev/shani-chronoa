# What Chronoa can do

Every capability in one place, generated from the same table the Help window
renders and the model is offered — `capabilities._GROUPS`. Nothing here is
written by hand, so it cannot claim something the build does not have.

**203 skills in 23 groups.** Each is a named, schema-typed module; the model
calls them by name. The whitelist *is* the design, so a new capability is a
new named skill. The one exception is `run_command`, for running a project's
tests and builds: off by default, sandboxed (no network, nothing outside the
project writable), one program per call and no shell syntax, and it asks before
every command.

Three things are not skills and are not here:

- **Senses** (`senses/`) perceive and deposit percepts; they are listed in the
  README, not callable as tools.
- **Trigger rules** watch for those percepts and act unattended.
- **The MCP server** exposes this same set to Claude Desktop, Claude Code and
  Cursor. It is a second entry point to the same skills, not more skills.

The **Before it runs** column is the honest part:

- **needs `<key>`** — the skill refuses until that switch is on in Settings →
  Privacy, and the refusal names the key, so a shut gate is never mistaken for a
  missing feature. Every one starts off: nothing that discloses or changes is
  available until you turn it on.
- **needs `<key>` to *do one thing*** — the skill gates one of its actions and
  not the rest. `write_text_file` creates a file with no permission (writing
  something new is not an edit) and needs the switch to replace one that already
  has content. It is written out because both of the shorter claims are false:
  "—" would say nothing gates it, and a bare key would say the whole skill is shut.
- **asks first, always** — the assistant asks before running it, and a standing
  "yes, for this session" grant does **not** cover it. This is on top of any
  switch, so a destructive skill is both gated and asked about.
- **—** — it runs when asked, with no switch and no prompt. Reading things and
  reporting them is in this group; that is the majority, deliberately.


**78 of 203 are consent-gated and 15 are destructive.**

## Files

Read, write, move, archive and undo files.

| Skill | What it does | Before it runs |
|---|---|---|
| `analyze_table` | Ask questions of a spreadsheet or CSV, and chart it | — |
| `apply_patch` | Apply a .patch/.diff file to files here | — |
| `broken_symlinks` | Which shortcuts point at nothing | — |
| `cleanup_apply` | Clear caches and unused Flatpak runtimes | **asks first, always** · needs `cleanup-enabled` |
| `cleanup_report` | What could be cleaned up | — |
| `compare_files` | Compare two files | — |
| `compute_hash` | Checksum a file | — |
| `convert_document` | Convert a document between md, txt, and html | — |
| `convert_media` | Convert audio, video and pictures | — |
| `create_archive` | Make or extract an archive | — |
| `create_directory` | Create a folder | — |
| `create_document` | Create a markdown, text, or HTML document | — |
| `dashboard` | Build a dashboard as a self-contained HTML page | — |
| `delete_file` | Delete a file or folder, recoverably from the trash | **asks first, always** · needs `file-delete-enabled` |
| `directory_tree` | Show a folder's shape | — |
| `duplicate_files` | Which files are stored twice, and how much that wastes | — |
| `edit_file` | Change one exact piece of text | **asks first, always** · needs `file-edit-enabled` |
| `email_draft` | Save an email draft as a local file, without sending | — |
| `email_read` | Read your mailbox: list recent messages or open one | needs `email-read-enabled` |
| `email_send` | Send an email from the configured account | needs `email-send-enabled` |
| `empty_directories` | Which directories hold nothing at all | — |
| `empty_trash` | Permanently empty the desktop trash | **asks first, always** · needs `trash-empty-enabled` |
| `extract_archive` | Unpack a tar, zip, 7z, rar or cab archive | — |
| `files_by_name` | Where every file with this name lives | — |
| `find_and_replace` | Find and replace | **asks first, always** · needs `bulk-edit-enabled` |
| `find_files` | Find files by name | — |
| `find_recently_modified` | What changed today | — |
| `get_file_info` | File size, age and permissions | — |
| `identify_file` | What this file really is, whatever its name says | — |
| `json_query` | Ask what a JSON document contains | — |
| `list_archive` | List what is inside an archive, without extracting it | — |
| `list_directory` | List a folder | — |
| `look_at_image` | Look at a picture and say what it shows | needs `vision-sense-enabled` |
| `manage_mount` | Mount or unmount | **asks first, always** · needs `mount-control-enabled` |
| `move_or_copy_file` | Move or copy | — |
| `notes` | Keep your own notes: add, list, search and remove | — |
| `office_document` | Read, create or edit Word, Excel and PowerPoint files | **asks first, always** · needs `file-edit-enabled` |
| `open_file` | Open a file | — |
| `pdf_pages` | Merge, split or take pages out of PDFs | — |
| `read_audio` | Read an audio file's length, format, and bitrate | — |
| `read_document` | Read a PDF or picture | — |
| `read_image` | Read a picture's dimensions, format, and colour | — |
| `read_text_file` | Read a text file | — |
| `read_video` | Read a video's length, size, and format | — |
| `search_documents` | Search inside your files with the desktop's own index | needs `document-search-enabled` |
| `search_file_contents` | Search inside files | — |
| `search_my_files` | Search your own files by meaning | needs `file-index-enabled` |
| `shopping_list` | Keep a shopping list: add, list, tick off and remove | — |
| `stale_files` | What is big AND untouched - the deletion candidates | — |
| `sync_folder` | Sync two folders, showing what would change first | — |
| `temp_files` | Find junk, temporary and leftover files by name | — |
| `undo_last_change` | Undo Chronoa's last change to a file | **asks first, always** · needs `file-edit-enabled` |
| `write_text_file` | Write a text file | needs `file-edit-enabled` to replace a file that already has content |

## Devices

Hardware, peripherals, sensors and their state.

| Skill | What it does | Before it runs |
|---|---|---|
| `adb_workflow` | Pair an Android phone and copy one file over adb | needs `phone-control-enabled` |
| `airplane_mode` | Report the radios, or turn airplane mode on or off | needs `radio-control-enabled` |
| `android_device` | Battery, screenshot, apps and files of a phone connected with adb | needs `phone-control-enabled` |
| `bluetooth_call` | Call through a paired phone, on this computer's speakers | needs `bluetooth-call-enabled` |
| `bluetooth_devices` | Bluetooth devices | — |
| `bluetooth_gatt` | Read a watch or band's battery, sensors and firmware | needs `bluetooth-gatt-enabled` |
| `find_device` | Make a lost watch or band buzz so you can find it | needs `bluetooth-gatt-enabled` |
| `fm_radio` | Listen to FM radio through a USB receiver | needs `fm-radio-enabled` |
| `ir_remote` | Send or receive infrared remote-control codes | needs `ir-remote-enabled` |
| `lan_phone` | Find phones on the same Wi-Fi network | needs `lan-phone-enabled` |
| `landline_call` | Call on your JioFiber landline, over Wi-Fi through the router | needs `landline-enabled` |
| `nfc` | Read an NFC tag, or write a link to a sticker | needs `nfc-enabled` |
| `phone` | Find, ping or send a file to your paired phone | needs `phone-control-enabled` |
| `phone_remote` | Press the phone camera shutter, media keys, or type on the phone | needs `phone-remote-enabled` |
| `print_queue` | The print queue, and cancelling a job | needs `print-control-enabled` |
| `screen_to_phone` | Show this screen on a phone | needs `screen-share-enabled` |
| `toggle_bluetooth` | Turn the Bluetooth adapter on or off | needs `bluetooth-control-enabled` |
| `toggle_wifi` | Turn the Wi-Fi radio on or off | needs `radio-control-enabled` |
| `watch` | Steps, sleep, stress, heart rate, SpO2 and settings on your watch | needs `bluetooth-gatt-enabled` |

## Everyday tools

The small things a person asks for most.

| Skill | What it does | Before it runs |
|---|---|---|
| `ask_user` | Ask a clarifying question | — |
| `barcode` | Make a Code 128 barcode or read codes in an image | — |
| `calculate` | Calculation | — |
| `convert_color` | Colour codes | — |
| `convert_units` | Convert units | — |
| `delegate` | Hand a long task to a helper and get back its summary | — |
| `download_file` | Download a file from a web address | — |
| `encode_text` | Encode or decode text | — |
| `explain_command` | Explain a command | — |
| `find_emoji` | Find an emoji | — |
| `generate_password` | Make a password | — |
| `qr_code` | Read a QR code or barcode, or make a QR code | — |
| `random_pick` | Coin, dice and random picks | — |
| `routines` | Save a phrase that runs a whole request | — |
| `scan_document` | Scan and read a page | — |
| `solve_math` | Algebra and calculus | — |
| `spell_word` | Spell a word | — |

## System

Processes, packages, updates, containers, vms, disks, network.

| Skill | What it does | Before it runs |
|---|---|---|
| `backup_adapters` | Check a backup repository or print a backup command without running it | — |
| `check_internet` | Is the internet working? | — |
| `cloud_files` | What is in this machine's cloud storage remotes | — |
| `connect_wifi` | Join a WiFi network | needs `wifi-connect-enabled` |
| `data_usage` | Data used | — |
| `disk_usage` | Report filesystem and directory space use | — |
| `list_wifi_networks` | WiFi networks | — |
| `login_history` | Who logged in recently | needs `sessions-sense-enabled` |
| `my_ip_address` | IP address | — |
| `open_settings` | Open the system Settings at a page | — |
| `print_file` | Print a file | — |
| `system_info` | Describe this machine | — |

## Time and reminders

The clock, dates, countdowns and alarms.

| Skill | What it does | Before it runs |
|---|---|---|
| `alarm` | Set, change, snooze or delete an alarm | — |
| `calendar_edit` | Add, move or cancel a calendar event | needs `calendar-write-enabled` |
| `calendar_events` | Read what is on your calendar | needs `calendar-read-enabled` |
| `calendar_month` | Month calendar | — |
| `date_math` | Date arithmetic | — |
| `get_datetime` | Date and time | — |
| `get_world_time` | Time somewhere else | — |
| `notify` | Send a notification | needs `notification-enabled` |
| `reminders` | Add, list, complete, and remove reminders | — |
| `set_timer` | Timer | — |
| `set_timezone` | Report or change the system timezone | needs `timezone-control-enabled` |
| `stopwatch` | Stopwatch | — |

## Code and git

Inspect repositories and work through files.

| Skill | What it does | Before it runs |
|---|---|---|
| `git_branch` | Create or switch branch | needs `git-write-enabled` |
| `git_commit` | Commit the files you name | needs `git-write-enabled` |
| `git_inspect` | What changed in a git repository | needs `git-sense-enabled` |
| `git_push` | Push a branch to a named remote | **asks first, always** · needs `git-push-enabled` |
| `manage_goals` | Save a multi-step goal to run later | needs `goals-enabled` |
| `manage_triggers` | Arm an automatic rule | **asks first, always** · needs `trigger-control-enabled` |
| `resolve_conflict` | What is conflicting in a git repository, and resolve it | needs `git-sense-enabled` |
| `run_command` | Run a project's tests, build or linter | **asks first, always** · needs `run-command-enabled` |
| `todo_list` | Keep a list of tasks to do | needs `todo-list-enabled` |

## Power and screen

Brightness, theme, power state, night light.

| Skill | What it does | Before it runs |
|---|---|---|
| `charger_info` | Say what is charging this machine, and how fast | — |
| `do_not_disturb` | Do Not Disturb | — |
| `get_battery_status` | Battery status | — |
| `lock_screen` | Lock this session | needs `screen-lock-enabled` |
| `power_action` | Suspend, restart or shut down | **asks first, always** · needs `power-control-enabled` |
| `set_brightness` | Screen brightness | — |
| `set_power_profile` | Power profile | — |
| `set_screensaver` | Change when the screen blanks and locks | needs `idle-timeout-enabled` |
| `set_sleep_inhibit` | Hold the machine awake for a bounded time | needs `sleep-inhibit-enabled` |

## Sound

Play, record, describe and edit audio.

| Skill | What it does | Before it runs |
|---|---|---|
| `audio_output` | Switch speakers, headphones and microphones | — |
| `get_volume` | Check the volume | — |
| `media_control` | Play, pause and skip media | — |
| `meeting` | Record a meeting and write notes | needs `meeting-enabled` |
| `set_mic_mute` | Mute or unmute the microphone input | needs `mic-control-enabled` |
| `set_mute` | Mute and unmute | — |
| `set_volume` | Change the volume | — |
| `sing` | Sing a line aloud | — |
| `speak` | Speak a reply aloud | — |

## Web

Search, fetch and act on a page.

| Skill | What it does | Before it runs |
|---|---|---|
| `browse` | Use a web page: click, type and read it | needs `web-sense-enabled` |
| `convert_currency` | Currency | — |
| `define_word` | What a word means | — |
| `get_location` | Where this computer is | — |
| `get_weather` | Weather | — |
| `lookup_wikipedia` | Who or what something is | — |
| `maps` | Find a place, get directions, or look up what is nearby | needs `web-sense-enabled` |
| `news` | Today's headlines, on a topic or in general | needs `web-sense-enabled` |
| `web_search` | Look something up | — |

## Appearance

Theme, wallpaper, scaling and the screen's own look.

| Skill | What it does | Before it runs |
|---|---|---|
| `accessibility` | Accessibility features | — |
| `desktop_setting` | Read or change a desktop setting (animations, clock, cursor, touchpad...) | needs `appearance-control-enabled` |
| `list_fonts` | Installed fonts, and which one is used | — |
| `set_locale` | System language and date, number and money formats | needs `locale-control-enabled` |
| `set_scaling` | Change the text size | needs `appearance-control-enabled` |
| `set_theme` | Switch the desktop between light and dark | needs `appearance-control-enabled` |
| `set_wallpaper` | Change the desktop wallpaper | needs `appearance-control-enabled` |
| `toggle_night_light` | Turn the blue-light filter on or off | needs `appearance-control-enabled` |

## Pointer and keyboard

Drive the desktop without the keyboard.

| Skill | What it does | Before it runs |
|---|---|---|
| `click_pointer` | Click | needs `input-control-enabled` |
| `drag` | Drag from one point to another | needs `input-control-enabled` |
| `list_shortcuts` | What the keyboard shortcuts on this machine are | — |
| `move_pointer` | Move the pointer | needs `input-control-enabled` |
| `scroll` | Scroll a window up, down or sideways | needs `input-control-enabled` |
| `set_keyboard_layout` | Change the keyboard layout | — |
| `type_text` | Type text | needs `input-control-enabled` |
| `ui_elements` | Press buttons, fill fields and open menus in other apps, and drive them visible | needs `input-control-enabled` |

## Processes and windows

Find, move and close what is open.

| Skill | What it does | Before it runs |
|---|---|---|
| `arrange_window` | Minimize, maximize, move or resize a window | — |
| `close_window` | Close a window | **asks first, always** · needs `window-close-enabled` |
| `focus_window` | Focus a window | — |
| `kill_process` | Stop a process | **asks first, always** · needs `process-kill-enabled` |
| `list_processes` | Running processes | — |
| `list_windows` | Open windows | — |
| `press_key` | Press a key | needs `input-control-enabled` |
| `process_cost` | What is using the CPU or the disk | — |

## Pictures

| Skill | What it does | Before it runs |
|---|---|---|
| `chart` | Make a chart, icon or diagram as an SVG file | — |
| `create_video` | Make a slideshow or colour clip | — |
| `edit_audio` | Trim, normalize, change volume or speed | — |
| `edit_image` | Resize, compress or edit a picture | — |
| `edit_video` | Trim, resize, rotate, or mute a video | — |
| `generate_image` | Make a new picture from a description, on this computer | — |

## What Chronoa knows

Memory, percepts and what it has decided.

| Skill | What it does | Before it runs |
|---|---|---|
| `conversations` | Search, reopen, copy or export earlier conversations | **asks first, always** · needs `file-delete-enabled` |
| `device_sync` | Carry conversations, preferences and memory to another machine in a file | — |
| `list_capabilities` | What it can do right now | — |
| `list_percepts` | What it perceives | — |
| `review_proposals` | What it noticed but did not keep | needs `memory-sense-enabled` |

## Apps

Launch, list and find applications.

| Skill | What it does | Before it runs |
|---|---|---|
| `default_apps` | Which app opens a kind of file, and the default browser | needs `default-apps-enabled` |
| `install_app` | Install and remove apps | needs `app-install-enabled` |
| `list_apps` | List installed applications | — |
| `open_application` | Open an app | — |

## Photos and video

Read, edit and extract from media.

| Skill | What it does | Before it runs |
|---|---|---|
| `photo_metadata` | When, where and with what camera a photo was taken | — |
| `photos` | Find your photos by what is in them, what is written on them, or when they were taken | — |
| `take_photo` | Take a photo or selfie with the webcam | needs `vision-sense-enabled` |

## Clipboard

Read and write the clipboard.

| Skill | What it does | Before it runs |
|---|---|---|
| `get_clipboard` | Read the clipboard | — |
| `set_clipboard` | Copy something | — |

## Local models

Choose, measure and rebuild the local models.

| Skill | What it does | Before it runs |
|---|---|---|
| `install_model` | Download a model | — |
| `recommend_model` | What model fits this machine | — |

## Privacy controls

Turn the disclosure switches.

| Skill | What it does | Before it runs |
|---|---|---|
| `fingerprint_status` | Whether fingerprint login is set up, and which fingers | — |
| `set_privacy` | Mute the microphone, disable a camera, or blank the screen | — |

## Seeing

| Skill | What it does | Before it runs |
|---|---|---|
| `capture_video` | Record a short video from the camera | — |
| `photo_video` | Find faces and objects in a photo or video, add effects, or go through a video | — |

## Sounds and recordings

Who spoke, and what was said.

| Skill | What it does | Before it runs |
|---|---|---|
| `recording` | What a sound is, or who said what in a recording | — |
| `speaker_id` | Enrol a voice and name who spoke, using the local speaker model | needs `hearing-sense-enabled` |

## Languages

Translate, spell and read text.

| Skill | What it does | Before it runs |
|---|---|---|
| `translate_text` | Translate text | — |

## Screen

Capture and describe the screen.

| Skill | What it does | Before it runs |
|---|---|---|
| `screenshot` | Screenshot | needs `vision-sense-enabled` |

