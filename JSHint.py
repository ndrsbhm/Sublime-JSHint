# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import sublime, sublime_plugin
import os, sys, subprocess, codecs, re, webbrowser
from threading import Timer

try:
  import commands
except ImportError:
  pass

PLUGIN_FOLDER = os.path.dirname(os.path.realpath(__file__))
RC_FILE = ".jshintrc"
SETTINGS_FILE = "JSHint.sublime-settings"
OUTPUT_VALID = b"*** JSHint output ***"

class JshintCommand(sublime_plugin.TextCommand):
  def run(self, edit, show_regions=True, show_panel=True):
    JshintListener.reset()

    # Make sure we're only linting javascript files.
    filePath = self.view.file_name()
    hasJsExtension = filePath != None and bool(re.search(r'\.jsm?$', filePath))
    hasJsSyntax = bool(re.search("JavaScript", self.view.settings().get("syntax"), re.I))
    if not hasJsExtension and not hasJsSyntax:
      return

    if PLUGIN_FOLDER.find(u".sublime-package") != -1:
      # Can't use this plugin if installed via the Package Manager in Sublime
      # Text 3, because it will be zipped into a .sublime-package archive.
      # Thus executing scripts *located inside this archive* via node.js
      # will, unfortunately, not be possible.
      url = "https://github.com/victorporof/Sublime-JSHint#manually"
      msg = """You won't be able to use this plugin in Sublime Text 3 when \
installed via the Package Manager.\n\nPlease remove it and install manually, \
following the instructions at:\n"""
      sublime.ok_cancel_dialog(msg + url)
      webbrowser.open(url)
      return

    # Get the current text in the buffer.
    bufferText = self.view.substr(sublime.Region(0, self.view.size()))
    # ...and save it in a temporary file. This allows for scratch buffers
    # and dirty files to be linted as well.
    namedTempFile = ".__temp__"
    tempPath = PLUGIN_FOLDER + "/" + namedTempFile
    print("Saving buffer to: " + tempPath)
    f = codecs.open(tempPath, mode='w', encoding='utf-8')
    f.write(bufferText)
    f.close()

    # Simply using `node` without specifying a path sometimes doesn't work :(
    settings = sublime.load_settings(SETTINGS_FILE)
    node = "node" if exists_in_path("node") else settings.get("node_path")

    output = ""
    try:
      print("Plugin folder is: " + PLUGIN_FOLDER)
      scriptPath = PLUGIN_FOLDER + "/scripts/run.js"
      output = get_output([node, scriptPath, tempPath, filePath or "?"])

      # Make sure the correct/expected output is retrieved.
      if output.find(OUTPUT_VALID) == -1:
        print(output)
        cmd = node + " " + scriptPath + " " + tempPath + " " + filePath
        msg = "Command " + cmd + " created invalid output"
        raise Exception(msg)

    except:
      # Something bad happened.
      print("Unexpected error({0}): {1}".format(sys.exc_info()[0], sys.exc_info()[1]))

      # Usually, it's just node.js not being found. Try to alleviate the issue.
      msg = "Node.js was not found in the default path. Please specify the location."
      if sublime.ok_cancel_dialog(msg):
        open_jshint_sublime_settings(self.view.window())
      else:
        msg = "You won't be able to use this plugin without specifying the path to Node.js."
        sublime.error_message(msg)
      return

    # Remove the output identification marker (first line).
    output = output[len(OUTPUT_VALID) + 1:]

    # We're done with linting, rebuild the regions shown in the current view.
    self.view.erase_regions("jshint_errors")
    os.remove(tempPath)

    if len(output) > 0:
      regions = []
      menuitems = []

      # For each line of jshint output (errors, warnings etc.) add a region
      # in the view and a menuitem in a quick panel.
      for line in output.decode().splitlines():
        try:
          lineNo, columnNo, description = line.split(" :: ")
          text_point = self.view.text_point(int(lineNo) - 1, int(columnNo))
          region = self.view.word(text_point)
          menuitems.append(lineNo + ":" + columnNo + " " + description)
          regions.append(region)
          JshintListener.errors.append((region, description))
        except:
          pass

      if show_regions:
        self.add_regions(regions)
      if show_panel:
        self.view.window().show_quick_panel(menuitems, self.on_chosen)

  def add_regions(self, regions):
    packageName = PLUGIN_FOLDER.replace(sublime.packages_path(), "")

    if int(sublime.version()) >= 3000:
      icon = "Packages/" + packageName + "/warning.png"
      self.view.add_regions("jshint_errors", regions, "keyword", icon,
        sublime.DRAW_EMPTY |
        sublime.DRAW_NO_FILL |
        sublime.DRAW_NO_OUTLINE |
        sublime.DRAW_SQUIGGLY_UNDERLINE |
        sublime.HIDE_ON_MINIMAP)
    else:
      icon = ".." + packageName + "/warning"
      self.view.add_regions("jshint_errors", regions, "keyword", icon,
        sublime.DRAW_EMPTY |
        sublime.DRAW_OUTLINED |
        sublime.HIDE_ON_MINIMAP)

  def on_chosen(self, index):
    if index == -1:
      return

    # Focus the user requested region from the quick panel.
    region = self.view.get_regions("jshint_errors")[index]
    selection = self.view.sel()
    selection.clear()
    selection.add(region)
    self.view.show(region)

class JshintSetLintingPrefsCommand(sublime_plugin.TextCommand):
  def run(self, edit):
    open_jshint_rc(self.view.window())

class JshintSetPluginOptionsCommand(sublime_plugin.TextCommand):
  def run(self, edit):
    open_jshint_sublime_settings(self.view.window())

class JshintSetNodePathCommand(sublime_plugin.TextCommand):
  def run(self, edit):
    open_jshint_sublime_settings(self.view.window())

class JshintClearAnnotationsCommand(sublime_plugin.TextCommand):
  def run(self, edit):
    JshintListener.reset()
    self.view.erase_regions("jshint_errors")

class JshintListener(sublime_plugin.EventListener):
  timer = None
  errors = []

  @staticmethod
  def reset():
    self = JshintListener

    # Invalidate any previously set timer.
    if self.timer != None:
      self.timer.cancel()

    self.timer = None
    self.errors = []

  @staticmethod
  def on_modified(view):
    self = JshintListener

    # Continue only if the source code was previously linted and the current
    # plugin settings allow this to happen. This is only available in Sublime 3.
    if int(sublime.version()) < 3000:
      return
    if not sublime.load_settings(SETTINGS_FILE).get("lint_on_edit"):
      return

    # Re-run the jshint command after a second of inactivity after the view
    # has been modified, to avoid regins getting out of sync with the actual
    # source code previously linted.
    if self.timer != None:
      self.timer.cancel()

    self.timer = Timer(1, lambda: view.window().run_command("jshint", { "show_panel": False }))
    self.timer.start()

  @staticmethod
  def on_post_save(view):
    # Continue only if the current plugin settings allow this to happen.
    if sublime.load_settings(SETTINGS_FILE).get("lint_on_save"):
      view.window().run_command("jshint", { "show_panel": False })

  @staticmethod
  def on_selection_modified(view):
    display_to_status_bar(view, JshintListener.errors)

def open_jshint_rc(window):
  window.open_file(PLUGIN_FOLDER + "/" + RC_FILE)

def open_jshint_sublime_settings(window):
  window.open_file(PLUGIN_FOLDER + "/" + SETTINGS_FILE)

def exists_in_path(cmd):
  # Can't search the path if a directory is specified.
  assert not os.path.dirname(cmd)
  path = os.environ.get("PATH", "").split(os.pathsep)
  extensions = os.environ.get("PATHEXT", "").split(os.pathsep)

  # For each directory in PATH, check if it contains the specified binary.
  for directory in path:
    base = os.path.join(directory, cmd)
    options = [base] + [(base + ext) for ext in extensions]
    for filename in options:
      if os.path.exists(filename):
        return True

  return False

def get_output(cmd):
  if int(sublime.version()) < 3000:
    if sublime.platform() != "windows":
      # Handle Linux and OS X in Python 2.
      run = '"' + '" "'.join(cmd) + '"'
      return commands.getoutput(run)
    else:
      # Handle Windows in Python 2.
      return subprocess.Popen(cmd, stdout=subprocess.PIPE).communicate()[0]
  else:
    # Handle all OS in Python 3.
    run = '"' + '" "'.join(cmd) + '"'
    return subprocess.check_output(run, stderr=subprocess.STDOUT, shell=True)

def display_to_status_bar(view, regions_to_descriptions):
  caret_region = view.sel()[0]

  for message_region, message_text in regions_to_descriptions:
    if message_region.intersects(caret_region):
      sublime.status_message(message_text)
      return
  else:
    sublime.status_message("")
