# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2017 Yorik van Havre <yorik@uncreated.net>              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************


"""This is Render workbench main module.

It provides the necessary objects to deal with rendering:
- GUI Commands
- Rendering Projects and Views
- A RendererHandler class to simplify access to external renderers modules

On initialization, this module will retrieve all renderer modules and create
the necessary UI controls.
"""


# ===========================================================================
#                                   Imports
# ===========================================================================


import sys
import os
import re
from os import path
from importlib import import_module
from tempfile import mkstemp
from types import SimpleNamespace
from operator import attrgetter

from PySide.QtGui import QAction, QIcon, QFileDialog
from PySide.QtCore import QT_TRANSLATE_NOOP, QObject, SIGNAL
import FreeCAD as App
import FreeCADGui as Gui
import Draft
import Part
import MeshPart
try:
    import ImageGui
except ImportError:
    pass
try:
    from draftutils.translate import translate  # 0.19
except ImportError:
    from Draft import translate  # 0.18

import camera
import lights


# ===========================================================================
#                                 Constants
# ===========================================================================


WBDIR = os.path.dirname(__file__)  # Workbench root directory
RENDERERS = [  # External renderers
    path.splitext(r)[0] for r in os.listdir(path.join(WBDIR, "renderers"))
    if not (".pyc" in r or "__" in r)]
# Paths to GUI resources
# This is for InitGui.py because it cannot import os
ICONPATH = os.path.join(WBDIR, "icons")
PREFPAGE = os.path.join(WBDIR, "ui", "RenderSettings.ui")


# ===========================================================================
#                     Core rendering objects (Project and View)
# ===========================================================================


class Project:
    """A rendering project"""

    # Related FeaturePython object has to be stored in a class variable,
    # (not in an instance variable...), otherwise it causes trouble in
    # serialization...
    _fpos = dict()

    def __init__(self, obj):
        obj.Proxy = self
        self.set_properties(obj)

    @property
    def fpo(self):
        """Underlying FeaturePython object getter"""
        return self._fpos[id(self)]

    @fpo.setter
    def fpo(self, new_fpo):
        """Underlying FeaturePython object setter"""
        self._fpos[id(self)] = new_fpo

    def set_properties(self, obj):
        """Set underlying FeaturePython object's properties

        Parameters
        ----------
        obj: FeaturePython Object related to this project
        """
        self.fpo = obj

        if "Renderer" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyString",
                "Renderer",
                "Render",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "The name of the raytracing engine to use"))

        if "DelayedBuild" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyBool",
                "DelayedBuild",
                "Render",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "If true, the views will be updated on render only"))
            obj.DelayedBuild = True

        if "Template" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyFile",
                "Template",
                "Render",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "The template to be used by this rendering"))

        if "PageResult" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyFileIncluded",
                "PageResult",
                "Render",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "The result file to be sent to the renderer"))

        if "Group" not in obj.PropertiesList:
            obj.addExtension("App::GroupExtensionPython", self)

        if "RenderWidth" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyInteger",
                "RenderWidth",
                "Render",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "The width of the rendered image in pixels"))
            parname = "User parameter:BaseApp/Preferences/Mod/Render"
            obj.RenderWidth = App.ParamGet(parname).GetInt("RenderWidth", 800)

        if "RenderHeight" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyInteger",
                "RenderHeight",
                "Render",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "The height of the rendered image in pixels"))
            par = "User parameter:BaseApp/Preferences/Mod/Render"
            obj.RenderHeight = App.ParamGet(par).GetInt("RenderHeight", 600)

        if "GroundPlane" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyBool",
                "GroundPlane",
                "Render",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "If true, a default ground plane will be added to the "
                    "scene"))
            obj.GroundPlane = False

        if "OutputImage" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyFile",
                "OutputImage",
                "Render",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "The image saved by this render"))

        if "OpenAfterRender" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyBool",
                "OpenAfterRender",
                "Render",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "If true, the rendered image is opened in FreeCAD after "
                    "the rendering is finished"))
            obj.GroundPlane = False
        obj.setEditorMode("PageResult", 2)

    def onDocumentRestored(self, obj):  # pylint: disable=no-self-use
        """Code to be executed when document is restored (callback)"""
        self.set_properties(obj)

    def execute(self, obj):  # pylint: disable=no-self-use
        """Code to be executed on document recomputation
        (callback, mandatory)
        """
        return True

    def onChanged(self, obj, prop):  # pylint: disable=no-self-use
        """Code to be executed when a property of the FeaturePython object is
        changed (callback)
        """
        if prop == "DelayedBuild" and not obj.DelayedBuild:
            for view in obj.Group:
                view.touch()

    @staticmethod
    def create(document, renderer, template=""):
        """Factory method to create a new rendering project.

        This method creates a new rendering project in a given FreeCAD
        Document.
        Providing a Document is mandatory: no rendering project should be
        created "off-ground".
        The method also creates the FeaturePython and the ViewProviderProject
        objects related to the new rendering project.

        Params:
        document:        the document where the project is to be created
        renderer:        the path to the renderer module to associate with
                         project
        template (opt.): the path to the rendering template to associate with
                         project

        Returns: the newly created Project, the related FeaturePython object
                 and the related ViewProviderProject
        """
        rdr = str(renderer)
        assert document, "Document must not be None"
        project_fpo = document.addObject("App::FeaturePython",
                                         "%sProject" % rdr)
        project = Project(project_fpo)
        project_fpo.Label = "%s Project" % rdr
        project_fpo.Renderer = rdr
        project_fpo.Template = str(template)
        viewp = ViewProviderProject(project_fpo.ViewObject)
        return project, project_fpo, viewp

    def write_groundplane(self, renderer):
        """Generate a ground plane rendering string for the scene

        For that purpose, dummy objects are temporarily added to the document
        which the project belongs to, and eventually deleted

        Parameters
        ----------
        renderer:   the renderer handler

        Returns
        -------
        The rendering string for the ground plane
        """
        result = ""
        doc = self.fpo.Document
        bbox = App.BoundBox()
        for view in self.fpo.Group:
            try:
                bbox.add(view.Source.Shape.BoundBox)
            except AttributeError:
                pass
        if bbox.isValid():
            # Create temporary object. We do this to keep renderers codes as
            # simple as possible: they only need to deal with one type of
            # object: RenderView objects
            margin = bbox.DiagonalLength / 2
            vertices = [App.Vector(bbox.XMin - margin, bbox.YMin - margin, 0),
                        App.Vector(bbox.XMax + margin, bbox.YMin - margin, 0),
                        App.Vector(bbox.XMax + margin, bbox.YMax + margin, 0),
                        App.Vector(bbox.XMin - margin, bbox.YMax + margin, 0)]
            vertices.append(vertices[0])  # Close the polyline...
            dummy1 = doc.addObject("Part::Feature", "dummygroundplane1")
            dummy1.Shape = Part.Face(Part.makePolygon(vertices))
            dummy2 = doc.addObject("App::FeaturePython", "dummygroundplane2")
            View(dummy2)
            dummy2.Source = dummy1
            ViewProviderView(dummy2.ViewObject)
            doc.recompute()

            result = renderer.get_rendering_string(dummy2)

            # Remove temp objects
            doc.removeObject(dummy2.Name)
            doc.removeObject(dummy1.Name)
            doc.recompute()

        return result

    def render(self, external=True):
        """Render the project, calling external renderer

        Parameters
        ----------
        external: switch between internal/external version of renderer

        Returns
        -------
        Output file path
        """
        obj = self.fpo

        # Get a handle to renderer module
        try:
            renderer = RendererHandler(obj.Renderer)
        except ModuleNotFoundError:
            msg = "Cannot render project: Renderer '%s' not found"\
                    % obj.Renderer
            App.Console.PrintError(msg)
            return ""

        # Get the rendering template
        assert (obj.Template and os.path.exists(obj.Template)),\
            "Cannot render project: Template not found"
        template = None
        with open(obj.Template, "r") as template_file:
            template = template_file.read()
        if sys.version_info.major < 3:
            template = template.decode("utf8")

        # Get a default camera, to be used if no camera is present in the scene
        camstr = (Gui.ActiveDocument.ActiveView.getCamera() if App.GuiUp
                  else camera.DEFAULT_CAMERA_STRING)
        dummycamview = SimpleNamespace()
        dummycamview.Source = SimpleNamespace()
        dummycamview.Source.Proxy = SimpleNamespace()
        dummycamview.Source.Proxy.type = "Camera"
        dummycamview.Name = "Default_Camera"
        camera.set_cam_from_coin_string(dummycamview.Source, camstr)
        cam = renderer.get_rendering_string(dummycamview)

        # Get objects rendering strings (including lights, cameras...)
        # and add a ground plane if required
        viewresult = (renderer.get_rendering_string if obj.DelayedBuild
                      else attrgetter("ViewResult"))
        objstrings = [viewresult(view) for view in obj.Group]

        if hasattr(obj, "GroundPlane") and obj.GroundPlane:
            objstrings.append(self.write_groundplane(renderer))

        renderobjs = '\n'.join(objstrings)

        # Merge all strings (cam, objects, ground plane...) into rendering
        # template
        if "RaytracingCamera" in template:
            template = re.sub("(.*RaytracingCamera.*)", cam, template)
            template = re.sub("(.*RaytracingContent.*)", renderobjs, template)
        else:
            template = re.sub("(.*RaytracingContent.*)",
                              cam + "\n" + renderobjs, template)
        template = (template.encode("utf8") if sys.version_info.major < 3
                    else template)

        # Write instantiated template into a temporary file
        fhandle, fpath = mkstemp(prefix=obj.Name,
                                 suffix=os.path.splitext(obj.Template)[-1])
        with open(fpath, "w") as fobj:
            fobj.write(template)
        os.close(fhandle)
        obj.PageResult = fpath
        os.remove(fpath)
        assert obj.PageResult, "Rendering error: No page result"

        App.ActiveDocument.recompute()

        # Fetch the rendering parameters
        params = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Render")
        prefix = params.GetString("Prefix", "")
        if prefix:
            prefix += " "

        try:
            output = obj.OutputImage
            assert output
        except (AttributeError, AssertionError):
            output = os.path.splitext(obj.PageResult)[0] + ".png"

        try:
            width = int(obj.RenderWidth)
        except (AttributeError, ValueError, TypeError):
            width = 800

        try:
            height = int(obj.RenderHeight)
        except (AttributeError, ValueError, TypeError):
            height = 600

        # Run the renderer on the generated temp file, with rendering params
        img = renderer.render(obj, prefix, external, output, width, height)

        # Open result in GUI if relevant
        try:
            if img and obj.OpenAfterRender:
                ImageGui.open(img)
        except (AttributeError, NameError):
            pass

        # And eventually return result path
        return img


class ViewProviderProject:
    """View provider for rendering project object"""

    def __init__(self, vobj):
        vobj.Proxy = self
        self.object = vobj.Object

    def attach(self, vobj):  # pylint: disable=no-self-use
        """Code to be executed when object is created/restored (callback)"""
        self.object = vobj.Object
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None

    def getDisplayModes(self, vobj):  # pylint: disable=no-self-use
        """Return a list of display modes (callback)"""
        return ["Default"]

    def getDefaultDisplayMode(self):  # pylint: disable=no-self-use
        """Return the name of the default display mode (callback).
        This display mode  must be defined in getDisplayModes.
        """
        return "Default"

    def setDisplayMode(self, mode):  # pylint: disable=no-self-use
        """Map the display mode defined in attach with those defined in
        getDisplayModes (callback).

        Since they have the same names nothing needs to be done.
        This method is optional
        """
        return mode

    def isShow(self):  # pylint: disable=no-self-use
        """Define the visibility of the object in the tree view (callback)"""
        return True

    def getIcon(self):  # pylint: disable=no-self-use
        """Return the icon which will appear in the tree view (callback)."""
        return os.path.join(WBDIR, "icons", "RenderProject.svg")

    def setupContextMenu(self, vobj, menu):  # pylint: disable=no-self-use
        """Setup the context menu associated to the object in tree view
        (callback)"""
        icon = QIcon(os.path.join(WBDIR, "icons", "Render.svg"))
        action1 = QAction(icon, "Render", menu)
        QObject.connect(action1, SIGNAL("triggered()"), self.render)
        menu.addAction(action1)

    def claimChildren(self):  # pylint: disable=no-self-use
        """Deliver the children belonging to this object (callback)"""
        try:
            return self.object.Group
        except AttributeError:
            pass

    def render(self):
        """Render project (call proxy render)"""
        try:
            self.object.Proxy.render()
        except AttributeError as err:
            App.Console.PrintError("Cannot render: {}".format(err))


class View:
    """A rendering view of FreeCAD object"""

    def __init__(self, obj):
        obj.addProperty("App::PropertyLink",
                        "Source",
                        "Render",
                        QT_TRANSLATE_NOOP("App::Property",
                                          "The source object of this view"))
        obj.addProperty("App::PropertyLink",
                        "Material",
                        "Render",
                        QT_TRANSLATE_NOOP("App::Property",
                                          "The material of this view"))
        obj.addProperty("App::PropertyString",
                        "ViewResult",
                        "Render",
                        QT_TRANSLATE_NOOP("App::Property",
                                          "The rendering output of this view"))
        obj.Proxy = self

    def execute(self, obj):  # pylint: disable=no-self-use
        """Code to be executed on document recomputation
        (callback, mandatory)

        Write or rewrite the ViewResult string if containing project is not
        'delayed build'
        """
        # Loop through View's containing projects, not DelayedBuild
        for proj in [x for x in obj.InList
                     if not getattr(x, "DelayedBuild", True)
                     and obj in getattr(x, "Group", [])
                     and hasattr(x, "Renderer")]:
            try:
                renderer = RendererHandler(proj.Renderer)
            except ModuleNotFoundError:
                continue

            # obj.ViewResult = proj.Proxy.write_object(obj, renderer)
            obj.ViewResult = renderer.get_rendering_string(obj)

    @staticmethod
    def create(fcd_obj, project):
        """Factory method to create a new rendering object in a given project.

        This method creates a new rendering object in a given rendering
        project, for a given FreeCAD object (of any type: Mesh, Part...).
        Please note that providing a Project is mandatory: no rendering
        view should be created "off-ground". Moreover, project's document
        and FreeCAD object document should be the same.
        The method also creates the FeaturePython and the ViewProviderView
        objects related to the new rendering view.

        Params:
        fcdobj:     the FreeCAD object for which the rendering view is to be
                    created
        project:    the rendering project in which the view is to be created

        Returns:    the newly created View, the related FeaturePython object
                    and the related ViewProviderView object
        """
        doc = project.Document
        assert doc == fcd_obj.Document,\
            "Unable to create View: Project and Object not in same document"
        fpo = doc.addObject("App::FeaturePython", "%sView" % fcd_obj.Name)
        fpo.Label = "View of %s" % fcd_obj.Name
        view = View(fpo)
        fpo.Source = fcd_obj
        project.addObject(fpo)
        viewp = ViewProviderView(fpo.ViewObject)
        return view, fpo, viewp


class ViewProviderView:
    """ViewProvider of rendering view object"""

    def __init__(self, vobj):
        vobj.Proxy = self
        self.object = None

    def attach(self, vobj):  # pylint: disable=no-self-use
        """Code to be executed when object is created/restored (callback)"""
        self.object = vobj.Object

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None

    def getDisplayModes(self, vobj):  # pylint: disable=no-self-use
        """Return a list of display modes (callback)"""
        return ["Default"]

    def getDefaultDisplayMode(self):  # pylint: disable=no-self-use
        """Return the name of the default display mode (callback).
        This display mode  must be defined in getDisplayModes.
        """
        return "Default"

    def setDisplayMode(self, mode):  # pylint: disable=no-self-use
        """Map the display mode defined in attach with those defined in
        getDisplayModes (callback).

        Since they have the same names nothing needs to be done. This method
        is optional
        """
        return mode

    def isShow(self):  # pylint: disable=no-self-use
        """Define the visibility of the object in the tree view (callback)"""
        return True

    def getIcon(self):  # pylint: disable=no-self-use
        """Return the icon which will appear in the tree view (callback)."""
        return os.path.join(WBDIR, "icons", "RenderViewTree.svg")


# ===========================================================================
#                            Renderer Handler
# ===========================================================================


class RendererHandler:
    """This class provides simplified access to external renderers modules.

    This class implements a simplified interface to external renderer module
    (façade design pattern).
    It requires a valid external renderer name for initialization, and
    provides:
    - a method to run the external renderer on a renderer-format file
    - a method to get a rendering string from an object's View, taking care of
      selecting the right method in renderer module according to
    view object's type.
    """
    def __init__(self, rdrname):
        self.renderer_name = str(rdrname)

        try:
            self.renderer_module = import_module("renderers." + rdrname)
        except ModuleNotFoundError:
            msg = translate(
                "Render", "Import Error: Renderer '%s' not found\n") % rdrname
            App.Console.PrintError(msg)
            raise

    def render(self, project, prefix, external, output, width, height):
        """Run the external renderer

        This method merely calls external renderer's 'render' method

        Params:
        - project:  the project to render
        - prefix:   a prefix string for call (will be inserted before path to
                    renderer)
        - external: a boolean indicating whether to call UI (true) or console
                    (false) version of renderer
        - width:    rendered image width, in pixels
        - height:   rendered image height, in pixels

        Return:     path to image file generated, or None if no image has been
                    issued by external renderer
        """
        return self.renderer_module.render(project,
                                           prefix,
                                           external,
                                           output,
                                           width,
                                           height)

    def get_rendering_string(self, view):
        """Provide a rendering string for the view of an object

        This method selects the specialized rendering method adapted for
        'view', according to its underlying object type, and calls it.

        Parameters:
        view: the view of the object to render

        Returns: a rendering string in the format of the external renderer
        for the supplied 'view'
        """

        if not view.Source:
            return ""

        # Special objects: camera, lights etc.
        try:
            objtype = view.Source.Proxy.type
        except AttributeError:
            pass
        else:
            if objtype == "PointLight":
                return self._render_pointlight(view)
            if objtype == "Camera":
                return self._render_camera(view)

        # General objects
        return self._render_object(view)

    def _render_object(self, view):
        """Get a rendering string for a generic FreeCAD object"""
        # get color and alpha
        mat = None
        color = None
        alpha = None
        if view.Material:
            mat = view.Material
        elif "Material" in view.Source.PropertiesList and view.Source.Material:
            mat = view.Source.Material
        if mat:
            if "Material" in mat.PropertiesList:
                if "DiffuseColor" in mat.Material:
                    color = mat.Material["DiffuseColor"]\
                               .strip("(")\
                               .strip(")")\
                               .split(",")[:3]
                if "Transparency" in mat.Material:
                    if float(mat.Material["Transparency"]) > 0:
                        alpha = 1.0 - float(mat.Material["Transparency"])
                    else:
                        alpha = 1.0

        if view.Source.ViewObject:
            vobj = view.Source.ViewObject
            if not color:
                if hasattr(vobj, "ShapeColor"):
                    color = vobj.ShapeColor[:3]
            if not alpha:
                if hasattr(vobj, "Transparency"):
                    if vobj.Transparency > 0:
                        alpha = 1.0 - float(vobj.Transparency) / 100.0
        if not color:
            color = (1.0, 1.0, 1.0)
        if not alpha:
            alpha = 1.0

        # get mesh
        mesh = None
        if hasattr(view.Source, "Group"):
            shps = [o.Shape for o in Draft.getGroupContents(view.Source)
                    if hasattr(o, "Shape")]
            mesh = MeshPart.meshFromShape(Shape=Part.makeCompound(shps),
                                          LinearDeflection=0.1,
                                          AngularDeflection=0.523599,
                                          Relative=False)
        elif view.Source.isDerivedFrom("Part::Feature"):
            mesh = MeshPart.meshFromShape(Shape=view.Source.Shape,
                                          LinearDeflection=0.1,
                                          AngularDeflection=0.523599,
                                          Relative=False)
        elif view.Source.isDerivedFrom("Mesh::Feature"):
            mesh = view.Source.Mesh
        if not mesh:
            return ""

        return self.renderer_module.write_object(view, mesh, color, alpha)

    def _render_camera(self, view):
        """Provide a rendering string for a camera.

        Parameters:
        view: a (valid) view of the camera to render.

        Returns: a rendering string, obtained from the renderer module
        """
        cam = view.Source
        asp_ratio = cam.AspectRatio
        pos = cam.Placement.Base
        rot = cam.Placement.Rotation
        target = pos.add(rot.multVec(App.Vector(0, 0, -1)).multiply(asp_ratio))
        updir = rot.multVec(App.Vector(0, 1, 0))
        name = view.Name
        return self.renderer_module.write_camera(pos, rot, updir, target, name)

    def _render_pointlight(self, view):
        """Gets a rendering string for a point light object

        Parameters:
        view: the view of the point light (contains the point light data)

        Returns: a rendering string, obtained from the renderer module
        """
        # get location, color, power
        try:
            location = view.Source.Location
            color = view.Source.Color
        except AttributeError:
            App.Console.PrintError(translate("Render",
                                             "Cannot render Point Light: "
                                             "Missing location and/or color "
                                             "attributes"))
            return ""
        # we accept missing Power (default value: 60)...
        power = getattr(view.Source, "Power", 60)

        # send everything to renderer module
        return self.renderer_module.write_pointlight(view,
                                                     location,
                                                     color,
                                                     power)


# ===========================================================================
#                               GUI Commands
# ===========================================================================


class RenderProjectCommand:
    """"Creates a rendering project.
    The renderer parameter must be a valid rendering module name
    """

    def __init__(self, renderer: str):
        # renderer must be a valid rendering module name (string)
        self.renderer = str(renderer)

    def GetResources(self):
        """Command's resources (callback)"""
        rdr = self.renderer
        return {
            "Pixmap": os.path.join(WBDIR, "icons", rdr + ".svg"),
            "MenuText": QT_TRANSLATE_NOOP("Render", "%s Project") % rdr,
            "ToolTip": QT_TRANSLATE_NOOP("Render", "Creates a %s "
                                                   "project") % rdr
            }

    def Activated(self):
        """Code to be executed when command is run (callback)
        Creates a new rendering project into active document
        """
        assert self.renderer, "Error: no renderer in command"

        # Get rendering template
        templates_folder = os.path.join(WBDIR, "templates")
        template_path = QFileDialog.getOpenFileName(
            Gui.getMainWindow(), "Select template", templates_folder, "*.*")
        template = template_path[0] if template_path else ""
        if not template:
            return

        # Create project
        Project.create(App.ActiveDocument, self.renderer, template)

        App.ActiveDocument.recompute()


class RenderViewCommand:
    """Creates a Raytracing view of the selected object(s) in the selected
    project or the default project
    """

    def GetResources(self):  # pylint: disable=no-self-use
        """Command's resources (callback)"""
        return {
            "Pixmap": os.path.join(WBDIR, "icons", "RenderView.svg"),
            "MenuText": QT_TRANSLATE_NOOP("Render", "Create View"),
            "ToolTip": QT_TRANSLATE_NOOP("Render",
                                         "Creates a Render view of the "
                                         "selected object(s) in the selected "
                                         "project or the default project")
            }

    def Activated(self):  # pylint: disable=no-self-use
        """Code to be executed when command is run (callback)"""
        project = None
        objs = []
        sel = Gui.Selection.getSelection()
        # Find project and objects to add to project
        for obj in sel:
            if "Renderer" in obj.PropertiesList:
                project = obj
            else:
                if (obj.isDerivedFrom("Part::Feature")
                        or obj.isDerivedFrom("Mesh::Feature")):
                    objs.append(obj)
                if (obj.isDerivedFrom("App::FeaturePython")
                        and hasattr(obj.Proxy, "type")
                        and obj.Proxy.type in ['PointLight', 'Camera']):
                    objs.append(obj)
        if not project:
            for obj in App.ActiveDocument.Objects:
                if "Renderer" in obj.PropertiesList:
                    project = obj
                    break
        if not project:
            App.Console.PrintError(translate("Render",
                                             "Unable to find a valid project "
                                             "in selection or document"))
            return

        # Add objects (as views) to the project
        for obj in objs:
            View.create(obj, project)
        App.ActiveDocument.recompute()


class RenderCommand:
    """Render a selected Render project"""

    def GetResources(self):  # pylint: disable=no-self-use
        """Command's resources (callback)"""
        return {"Pixmap": os.path.join(WBDIR, "icons", "Render.svg"),
                "MenuText": QT_TRANSLATE_NOOP("Render", "Render"),
                "ToolTip": QT_TRANSLATE_NOOP("Render",
                                             "Performs the render of a "
                                             "selected project or the default "
                                             "project")}

    def Activated(self):  # pylint: disable=no-self-use
        """Code to be executed when command is run (callback)"""
        # Find project
        project = None
        sel = Gui.Selection.getSelection()
        for obj in sel:
            if "Renderer" in obj.PropertiesList:
                project = obj
                break
        if not project:
            for obj in App.ActiveDocument.Objects:
                if "Renderer" in obj.PropertiesList:
                    return

        # Render (and display if required)
        project.Proxy.render()


class CameraCommand:
    """Create a Camera object"""

    def GetResources(self):  # pylint: disable=no-self-use
        """Command's resources (callback)"""

        return {"Pixmap": ":/icons/camera-photo.svg",
                "MenuText": QT_TRANSLATE_NOOP("Render", "Create Camera"),
                "ToolTip": QT_TRANSLATE_NOOP("Render",
                                             "Creates a Camera object from "
                                             "the current camera position")}

    def Activated(self):  # pylint: disable=no-self-use
        """Code to be executed when command is run (callback)"""
        camera.Camera.create()


class PointLightCommand:
    """Create a Point Light object"""

    def GetResources(self):  # pylint: disable=no-self-use
        """Command's resources (callback)"""

        return {"Pixmap": os.path.join(WBDIR, "icons", "PointLight.svg"),
                "MenuText": QT_TRANSLATE_NOOP("Render", "Create Point Light"),
                "ToolTip": QT_TRANSLATE_NOOP("Render",
                                             "Creates a Point Light object")}

    def Activated(self):  # pylint: disable=no-self-use
        """Code to be executed when command is run (callback)"""
        lights.PointLight.create()

# ===========================================================================
#                            Module initialization
# ===========================================================================


# If Gui is up, create the FreeCAD commands
if App.GuiUp:
    # Add commands
    RENDER_COMMANDS = []
    for rend in RENDERERS:
        Gui.addCommand('Render_' + rend, RenderProjectCommand(rend))
        RENDER_COMMANDS.append('Render_' + rend)
    RENDER_COMMANDS.append("Separator")
    for cmd in (("Camera", CameraCommand()),
                ("PointLight", PointLightCommand())):
        Gui.addCommand(*cmd)
        RENDER_COMMANDS.append(cmd[0])
    RENDER_COMMANDS.append("Separator")
    for cmd in (("View", RenderViewCommand()),
                ("Render", RenderCommand())):
        Gui.addCommand(*cmd)
        RENDER_COMMANDS.append(cmd[0])

# vim: foldmethod=indent
