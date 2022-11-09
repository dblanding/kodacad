#!/usr/bin/env python
#
# Copyright 2022 Doug Blanding (dblanding@gmail.com)
#
# This file is part of kodacad.
# The latest  version of this file can be found at:
# //https://github.com/dblanding/kodacad
#
# kodacad is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# kodacad is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# if not, write to the Free Software Foundation, Inc.
# 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#


import logging
import math
import pprint
import sys

from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
from OCC.Core.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
from OCC.Core.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakePrism,
    BRepPrimAPI_MakeRevol,
)
from OCC.Core.gp import gp_Ax1, gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopoDS import topods_Edge, topods_Face, topods_Vertex
from OCC.Core.TopTools import TopTools_ListOfShape
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import QApplication, QMenu, QTreeWidgetItemIterator

from m2d import M2D
import stepanalyzer
from mainwindow import MainWindow, doc
from OCCUtils import Topology
import workplane

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # set to DEBUG | INFO | ERROR

TOL = 1e-7  # Linear Tolerance
ATOL = TOL  # Angular Tolerance
print("TOLERANCE = ", TOL)
# DEFAULT_COLOR = Quantity_ColorRGBA(0.6, 0.6, 0.4, 1.0)
DEFAULT_COLOR = Quantity_Color(0.6, 0.6, 0.4, Quantity_TOC_RGB)


#############################################
#
# Workplane creation functions
#
#############################################


def wpBy3Pts(*args):
    """Direction from pt1 to pt2 sets wDir, pt2 is wpOrigin.
    Direction from pt2 to pt3 sets uDir."""
    prev_uid = win.activeWpUID  # uid of currently active workplane
    if win.ptStack:
        # Finish
        p3 = win.ptStack.pop()
        p2 = win.ptStack.pop()
        p1 = win.ptStack.pop()
        wVec = gp_Vec(p1, p2)
        wDir = gp_Dir(wVec)
        origin = p2
        uVec = gp_Vec(p2, p3)
        uDir = gp_Dir(uVec)
        axis3 = gp_Ax3(origin, wDir, uDir)
        wp = workplane.WorkPlane(100, ax3=axis3)
        new_uid = win.get_wp_uid(wp)
        display_new_active_wp(prev_uid, new_uid)
        win.clearCallback()
    else:
        # Initial setup
        win.registerCallback(wpBy3PtsC)
        display.selected_shape = None
        display.SetSelectionModeVertex()
        statusText = "Pick 3 points. Dir from pt1-pt2 sets wDir, pt2 is origin."
        win.statusBar().showMessage(statusText)
        return


def wpBy3PtsC(shapeList, *args):
    """Callbask (collector) for wpBy3Pts"""
    for shape in shapeList:
        vrtx = topods_Vertex(shape)
        gpPt = BRep_Tool.Pnt(vrtx)  # convert vertex to gp_Pnt
        win.ptStack.append(gpPt)
    if len(win.ptStack) == 1:
        statusText = "Now select point 2 (wp origin)."
        win.statusBar().showMessage(statusText)
    elif len(win.ptStack) == 2:
        statusText = "Now select point 3 to set uDir."
        win.statusBar().showMessage(statusText)
    elif len(win.ptStack) == 3:
        wpBy3Pts()


def wpOnFace(*args):
    """ First face defines plane of wp. Second face defines uDir."""
    prev_uid = win.activeWpUID  # uid of currently active workplane
    if not win.faceStack:
        win.registerCallback(wpOnFaceC)
        display.selected_shape = None
        display.SetSelectionModeFace()
        statusText = "Select face for workplane."
        win.statusBar().showMessage(statusText)
        return
    faceU = win.faceStack.pop()
    faceW = win.faceStack.pop()
    wp = workplane.WorkPlane(100, face=faceW, faceU=faceU)
    new_uid = win.get_wp_uid(wp)
    display_new_active_wp(prev_uid, new_uid)
    win.clearCallback()


def wpOnFaceC(shapeList, *args):
    """Callback (collector) for wpOnFace"""
    if not shapeList:
        shapeList = []
    for shape in shapeList:
        face = topods_Face(shape)
        win.faceStack.append(face)
    if len(win.faceStack) == 1:
        statusText = "Select face for workplane U direction."
        win.statusBar().showMessage(statusText)
    elif len(win.faceStack) == 2:
        wpOnFace()


def makeWP():
    """Default workplane located in X-Y plane at 0,0,0"""
    prev_uid = win.activeWpUID  # uid of currently active workplane
    wp = workplane.WorkPlane(100)
    new_uid = win.get_wp_uid(wp)
    display_new_active_wp(prev_uid, new_uid)


def display_new_active_wp(prev_uid, new_uid):
    """Display new active wp & redraw previous active wp if it is displayed."""
    # If currently active wp is displayed, redraw to show its new border color
    if prev_uid and prev_uid not in win.hide_list:
        win.redraw_workplanes()
    else:
        win.draw_wp(new_uid)


#############################################
#
# 3D Geometry creation functions
#
#############################################


def get_tag_of_active_asy():
    """Get tag of active assy, if any, else top assembly"""
    tag = 1  # value of tag for first label at root (default)
    act_asy_uid = win.activeAsyUID
    if act_asy_uid:
        if doc.label_dict[act_asy_uid]["is_assy"]:
            ref_entry = doc.label_dict[act_asy_uid]["ref_entry"]
            if ref_entry:
                tag = int(ref_entry.split(':')[-1])
    return tag


def get_inv_loc_of_active_asy():
    """Get inverse location vector, if any, of active assembly"""
    loc = TopLoc_Location()
    act_asy_uid = win.activeAsyUID
    if act_asy_uid:
        loc = doc.label_dict[act_asy_uid]["inv_loc"]
    return loc


def extrude():
    """Extrude profile on active WP to create a new part.
    Add new part to active assembly, if any, else to Top"""
    tag = get_tag_of_active_asy()
    loc = get_inv_loc_of_active_asy()
    wp = win.activeWp
    if len(win.lineEditStack) == 2:
        name = win.lineEditStack.pop()
        length = float(win.lineEditStack.pop()) * win.unitscale
        wireOK = wp.makeWire()
        if not wireOK:
            print("Unable to make wire.")
            return
        myFaceProfile = BRepBuilderAPI_MakeFace(wp.wire)
        aPrismVec = wp.wVec * length
        new_part = BRepPrimAPI_MakePrism(myFaceProfile.Shape(), aPrismVec).Shape()
        loc_new_part = BRepBuilderAPI_Transform(new_part, loc.Transformation()).Shape()
        uid = doc.add_component_to_asy(loc_new_part, name, DEFAULT_COLOR, tag)
        win.build_tree()
        win.setActivePart(uid)
        win.draw_shape(uid)
        win.syncUncheckedToHideList()
        win.statusBar().showMessage("New part created.")
        win.clearCallback()
    else:
        win.registerCallback(extrudeC)
        win.lineEdit.setFocus()
        statusText = "Enter extrusion length, then enter part name."
        win.statusBar().showMessage(statusText)


def extrudeC(shapeList, *args):
    """Callback (collector) for extrude"""
    win.lineEdit.setFocus()
    if len(win.lineEditStack) == 2:
        extrude()


def revolve():
    """Revolve profile on active WP to create a new part.
    Add new part to active assembly, if any, else to Top"""
    tag = get_tag_of_active_asy()
    loc = get_inv_loc_of_active_asy()
    wp = win.activeWp
    if win.lineEditStack and len(win.ptStack) == 2:
        p2 = win.ptStack.pop()
        p1 = win.ptStack.pop()
        name = win.lineEditStack.pop()
        win.clearAllStacks()
        wireOK = wp.makeWire()
        if not wireOK:
            print("Unable to make wire.")
            return
        face = BRepBuilderAPI_MakeFace(wp.wire).Shape()
        revolve_axis = gp_Ax1(p1, gp_Dir(gp_Vec(p1, p2)))
        new_part = BRepPrimAPI_MakeRevol(face, revolve_axis).Shape()
        loc_new_part = BRepBuilderAPI_Transform(new_part, loc.Transformation()).Shape()
        uid = doc.add_component_to_asy(loc_new_part, name, DEFAULT_COLOR, tag)
        win.build_tree()
        win.setActivePart(uid)
        win.draw_shape(uid)
        win.syncUncheckedToHideList()
        win.statusBar().showMessage("New part created.")
        win.clearCallback()
    else:
        win.registerCallback(revolveC)
        display.SetSelectionModeVertex()
        win.lineEdit.setFocus()
        statusText = "Pick two points on revolve axis."
        win.statusBar().showMessage(statusText)


def revolveC(shapeList, *args):
    """Callback (collector) for revolve"""
    for shape in shapeList:
        vrtx = topods_Vertex(shape)
        gpPt = BRep_Tool.Pnt(vrtx)  # convert vertex to gp_Pnt
        win.ptStack.append(gpPt)
    if len(win.ptStack) == 1:
        statusText = "Select 2nd point on revolve axis."
        win.statusBar().showMessage(statusText)
    elif len(win.ptStack) == 2 and not win.lineEditStack:
        statusText = "Enter part name."
        win.statusBar().showMessage(statusText)
    win.lineEdit.setFocus()
    if win.lineEditStack and len(win.ptStack) == 2:
        revolve()


#############################################
#
# 3D Geometry positioning functons
#
#############################################


def rotateAP():
    """Experimental... Rotate active part incrementally"""
    ax1 = gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(1.0, 0.0, 0.0))
    aRotTrsf = gp_Trsf()
    angle = math.pi / 18  # 10 degrees
    aRotTrsf.SetRotation(ax1, angle)
    aTopLoc = TopLoc_Location(aRotTrsf)
    uid = win.activePartUID
    win.erase_shape(uid)
    win.activePart.Move(aTopLoc)
    win.draw_shape(uid)

def rev_rotateAP():
    """Experimental... rotate back"""
    ax1 = gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(1.0, 0.0, 0.0))
    aRotTrsf = gp_Trsf()
    angle = math.pi / 18  # 10 degrees
    aRotTrsf.SetRotation(ax1, angle)
    aTopLoc = TopLoc_Location(aRotTrsf)
    aTopLoc = aTopLoc.Inverted()
    uid = win.activePartUID
    win.erase_shape(uid)
    win.activePart.Move(aTopLoc)
    win.draw_shape(uid)


#############################################
#
# 3D Geometry modification functions
#
#############################################


def mill():
    """Mill profile on active WP into active part."""
    wp = win.activeWp
    if win.lineEditStack:
        depth = float(win.lineEditStack.pop()) * win.unitscale
        wireOK = wp.makeWire()
        if not wireOK:
            print("Unable to make wire.")
            return
        wire = wp.wire
        workPart = win.activePart
        uid = win.activePartUID
        punchProfile = BRepBuilderAPI_MakeFace(wire)
        aPrismVec = wp.wVec * -depth
        tool = BRepPrimAPI_MakePrism(punchProfile.Shape(), aPrismVec).Shape()
        newPart = BRepAlgoAPI_Cut(workPart, tool).Shape()
        win.erase_shape(uid)
        doc.replace_shape(uid, newPart)
        win.draw_shape(uid)
        win.setActivePart(uid)
        win.statusBar().showMessage("Mill operation complete")
        win.clearCallback()
    else:
        win.registerCallback(millC)
        win.lineEdit.setFocus()
        statusText = "Enter milling depth (pos in -w direction)"
        win.statusBar().showMessage(statusText)


def millC(shapeList, *args):
    """Callback (collector) for mill"""
    win.lineEdit.setFocus()
    if win.lineEditStack:
        mill()


def pull():
    """Pull profile on active WP onto active part."""
    wp = win.activeWp
    if win.lineEditStack:
        length = float(win.lineEditStack.pop()) * win.unitscale
        wireOK = wp.makeWire()
        if not wireOK:
            print("Unable to make wire.")
            return
        wire = wp.wire
        workPart = win.activePart
        uid = win.activePartUID
        pullProfile = BRepBuilderAPI_MakeFace(wire)
        aPrismVec = wp.wVec * length
        tool = BRepPrimAPI_MakePrism(pullProfile.Shape(), aPrismVec).Shape()
        newPart = BRepAlgoAPI_Fuse(workPart, tool).Shape()
        win.erase_shape(uid)
        doc.replace_shape(uid, newPart)
        win.draw_shape(uid)
        win.setActivePart(uid)
        win.statusBar().showMessage("Pull operation complete")
        win.clearCallback()
    else:
        win.registerCallback(pullC)
        win.lineEdit.setFocus()
        statusText = "Enter pull distance (pos in +w direction)"
        win.statusBar().showMessage(statusText)


def pullC(shapeList, *args):
    """Callback (collector) for pull"""
    win.lineEdit.setFocus()
    if win.lineEditStack:
        pull()


def fillet(event=None):
    """Fillet (blend) edges of active part"""
    if win.lineEditStack and win.edgeStack:
        topo = Topology.Topo(win.activePart)
        text = win.lineEditStack.pop()
        try:
            fillet_r = float(text) * win.unitscale
        except ValueError:
            print(f"Expected a number. You entered '{text}'")
            win.clearCallback()
            return
        edges = []
        # Test if edge(s) selected are in active part
        for edge in win.edgeStack:
            try:
                if edge in topo.edges():
                    edges.append(edge)
                else:
                    print("Selected edge(s) must be in Active Part.")
                    win.clearCallback()
                    return
            except ValueError:
                print("You must first set the Active Part.")
                win.clearCallback()
                return
        win.edgeStack = []
        workPart = win.activePart
        uid = win.activePartUID
        mkFillet = BRepFilletAPI_MakeFillet(workPart)
        for edge in edges:
            mkFillet.Add(fillet_r, edge)
        try:
            newPart = mkFillet.Shape()
            win.erase_shape(uid)
            doc.replace_shape(uid, newPart)
            win.draw_shape(uid)
            win.statusBar().showMessage("Fillet operation complete")
        except RuntimeError as e:
            print(f"Unable to make Fillet. {e}")
        win.setActivePart(uid)
        win.clearCallback()
    else:
        win.registerCallback(filletC)
        display.SetSelectionModeEdge()
        statusText = "Select edge(s) to fillet then specify fillet radius."
        win.statusBar().showMessage(statusText)


def filletC(shapeList, *args):
    """Callback (collector) for fillet"""
    win.lineEdit.setFocus()
    for shape in shapeList:
        edge = topods_Edge(shape)
        win.edgeStack.append(edge)
    if win.edgeStack and win.lineEditStack:
        fillet()


def fuse():
    """Fuse an adjacent or overlapping solid shape to active part."""
    if win.shapeStack:
        shape = win.shapeStack.pop()
        workpart = win.activePart
        uid = win.activePartUID
        newPart = BRepAlgoAPI_Fuse(workpart, shape).Shape()
        win.erase_shape(uid)
        doc.replace_shape(uid, newPart)
        win.draw_shape(uid)
        win.setActivePart(uid)
        win.statusBar().showMessage("Fuse operation complete")
        win.clearCallback()
    else:
        win.registerCallback(fuseC)
        statusText = "Select shape to fuse to active part."
        win.statusBar().showMessage(statusText)


def fuseC(shapeList, *args):
    """Callback (collector) for fuse"""
    for shape in shapeList:
        win.shapeStack.append(shape)
    if win.shapeStack:
        fuse()


def shell(event=None):
    """Shell active part"""
    if win.lineEditStack and win.faceStack:
        text = win.lineEditStack.pop()
        faces = TopTools_ListOfShape()
        for face in win.faceStack:
            faces.Append(face)
        win.faceStack = []
        workPart = win.activePart
        uid = win.activePartUID
        shellT = float(text) * win.unitscale
        newPart = BRepOffsetAPI_MakeThickSolid(workPart, faces, -shellT, 1.0e-3).Shape()
        win.erase_shape(uid)
        doc.replace_shape(uid, newPart)
        win.draw_shape(uid)
        win.setActivePart(uid)
        win.statusBar().showMessage("Shell operation complete")
        win.clearCallback()
    else:
        win.registerCallback(shellC)
        display.SetSelectionModeFace()
        statusText = "Select face(s) to remove then specify shell thickness."
        win.statusBar().showMessage(statusText)


def shellC(shapeList, *args):
    """Callback (collector) for shell"""
    win.lineEdit.setFocus()
    for shape in shapeList:
        face = topods_Face(shape)
        win.faceStack.append(face)
    if win.faceStack and win.lineEditStack:
        shell()


#############################################
#
#  Save / Open / Load functions
#
#############################################


def open_doc():
    doc.open_doc()
    win.build_tree()


def save_doc():
    doc.save_doc()


def load_stp_at_top():
    """Load STEP file and assign it to self.doc
    This effectively allows step to be a surrogate for file save/load."""
    win.setActivePart(0)
    win.setActiveAsy(0)
    doc.load_stp_at_top()
    win.build_tree()
    win.redraw()
    win.fitAll()


def load_stp_cmpnt():
    """Load root level shape(s) in step file as component(s) under top."""
    doc.load_stp_cmpnt()
    win.build_tree()
    win.redraw()
    win.fitAll()


def load_stp_undr_top():
    """Copy root label (with located components) of step file under top."""
    doc.load_stp_undr_top()
    win.build_tree()
    win.redraw()
    win.fitAll()


#############################################
#
#  Info & Utility functions
#
#############################################


def print_uid_dict():
    pprint.pprint(doc.label_dict)


def print_part_dict():
    pprint.pprint(doc.part_dict)


def dumpDoc():
    sa = stepanalyzer.StepAnalyzer(document=doc.doc)
    dumpdata = sa.dump()
    print(dumpdata)


def topoDumpAP():
    if win.activePart:
        Topology.dumpTopology(win.activePart)


def printActiveAsyInfo():
    uid = win.activeAsyUID
    if uid:
        name = doc.label_dict[uid]["name"]
        print(f"Active Assembly (uid) Name: ({uid}) {name}")
    else:
        print("None active")


def printActiveWpInfo():
    uid = win.activeWpUID
    if uid:
        name = win.activeWp
        print(f"Active WP (uid) Name: ({uid}) {name}")
    else:
        print("None active")


def printActivePartInfo():
    uid = win.activePartUID
    if uid:
        name = doc.label_dict[uid]["name"]
        print(f"Active Part (uid) Name: ({uid}) {name}")
    else:
        print("None active")


def printActPart():
    uid = win.activePartUID
    if uid:
        name = win.label_dict[uid]["name"]
        print(f"Active Part: {name} [{uid}]")
    else:
        print(None)


def printTreeView():
    """Print 'uid'; 'name'; 'parent' for all items in treeView."""
    iterator = QTreeWidgetItemIterator(win.treeView)
    while iterator.value():
        item = iterator.value()
        name = item.text(0)
        uid = item.text(1)
        pname = None
        parent = item.parent()
        if parent:
            puid = parent.text(1)
            pname = parent.text(0)
        print(f"UID: {uid}; Name: {name}; Parent: {pname}")
        iterator += 1


def printDrawList():
    print("Draw List:", win.drawList)


def printInSync():
    print(win.inSync())


def setUnits_in():
    win.setUnits("in")


def setUnits_mm():
    win.setUnits("mm")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    menu = win.menuBar()
    file_menu = win.add_menu("File")
    win.add_function_to_menu("File", "Open File", open_doc)
    win.add_function_to_menu("File", "Save File", save_doc)
    file_menu.addSeparator()
    win.add_function_to_menu("File", "Load STEP At Top", load_stp_at_top)
    win.add_function_to_menu("File", "Load STEP Under Top", load_stp_undr_top)
    win.add_function_to_menu("File", "Load STEP Component", load_stp_cmpnt)
    win.add_function_to_menu("File", "Save STEP (Top)", doc.save_step_doc)
    win.add_menu("Workplane")
    win.add_function_to_menu("Workplane", "At Origin, XY Plane", makeWP)
    win.add_function_to_menu("Workplane", "On face", wpOnFace)
    win.add_function_to_menu("Workplane", "By 3 points", wpBy3Pts)
    win.add_menu("Create 3D")
    win.add_function_to_menu("Create 3D", "Extrude", extrude)
    win.add_function_to_menu("Create 3D", "Revolve", revolve)
    win.add_menu("Modify Active Part")
    win.add_function_to_menu("Modify Active Part", "Rotate Act Part", rotateAP)
    win.add_function_to_menu("Modify Active Part", "Reverse Rotate Act Part", rev_rotateAP)
    win.add_function_to_menu("Modify Active Part", "Mill", mill)
    win.add_function_to_menu("Modify Active Part", "Pull", pull)
    win.add_function_to_menu("Modify Active Part", "Fillet", fillet)
    win.add_function_to_menu("Modify Active Part", "Shell", shell)
    win.add_function_to_menu("Modify Active Part", "Fuse", fuse)
    win.add_menu("Utility")
    win.add_function_to_menu("Utility", "print label_dict", print_uid_dict)
    win.add_function_to_menu("Utility", "print part_dict", print_part_dict)
    win.add_function_to_menu("Utility", "dump doc", dumpDoc)
    win.add_function_to_menu("Utility", "Topology of Act Prt", topoDumpAP)
    win.add_function_to_menu("Utility", "print(Active Wp Info)", printActiveWpInfo)
    win.add_function_to_menu("Utility", "print(Active Asy Info)", printActiveAsyInfo)
    win.add_function_to_menu("Utility", "print(Active Prt Info)", printActivePartInfo)
    win.add_function_to_menu("Utility", "Clear Line Edit Stack", win.clearLEStack)
    win.add_function_to_menu("Utility", "Calculator", win.launchCalc)
    win.add_function_to_menu("Utility", "set Units ->in", setUnits_in)
    win.add_function_to_menu("Utility", "set Units ->mm", setUnits_mm)

    drawSubMenu = QMenu("Draw")
    win.popMenu.addMenu(drawSubMenu)
    drawSubMenu.addAction("Fit", win.fitAll)

    win.treeView.popMenu.addAction("Item Info", win.showClickedInfo)
    win.treeView.popMenu.addAction("Set Active", win.setClickedActive)
    win.treeView.popMenu.addAction("Make Transparent", win.setTransparent)
    win.treeView.popMenu.addAction("Make Opaque", win.setOpaque)
    win.treeView.popMenu.addAction("Edit Name", win.editName)

    win.show()
    win.canvas.InitDriver()
    display = win.canvas._display
    a2d = M2D(win, display)

    selectSubMenu = QMenu("Select Mode")
    win.popMenu.addMenu(selectSubMenu)
    selectSubMenu.addAction("Vertex", display.SetSelectionModeVertex)
    selectSubMenu.addAction("Edge", display.SetSelectionModeEdge)
    selectSubMenu.addAction("Face", display.SetSelectionModeFace)
    selectSubMenu.addAction("Shape", display.SetSelectionModeShape)
    selectSubMenu.addAction("Neutral", display.SetSelectionModeNeutral)
    win.popMenu.addAction("Clear Callback", win.clearCallback)
    # Construction Line Toolbar buttons
    win.wcToolBar.addAction(QIcon(QPixmap("icons/hcl.gif")), "Horizontal", a2d.clineH)
    win.wcToolBar.addAction(QIcon(QPixmap("icons/vcl.gif")), "Vertical", a2d.clineV)
    win.wcToolBar.addAction(QIcon(QPixmap("icons/hvcl.gif")), "H + V", a2d.clineHV)
    win.wcToolBar.addAction(
        QIcon(QPixmap("icons/tpcl.gif")), "By 2 Pnts", a2d.cline2Pts
    )
    win.wcToolBar.addAction(QIcon(QPixmap("icons/acl.gif")), "Angled", a2d.clineAng)
    # win.wcToolBar.addAction(QIcon(QPixmap('icons/refangcl.gif')), 'Ref-Ang', a2d.clineRefAng)
    # win.wcToolBar.addAction(QIcon(QPixmap('icons/abcl.gif')), 'Angular Bisector', a2d.clineAngBisec)
    win.wcToolBar.addAction(
        QIcon(QPixmap("icons/lbcl.gif")), "Linear Bisector", a2d.clineLinBisec
    )
    # win.wcToolBar.addAction(QIcon(QPixmap('icons/parcl.gif')), 'Parallel', a2d.clinePara)
    # win.wcToolBar.addAction(QIcon(QPixmap('icons/perpcl.gif')), 'Perpendicular', a2d.clinePerp)
    # win.wcToolBar.addAction(QIcon(QPixmap('icons/cltan1.gif')), 'Tangent to circle', a2d.clineTan1)
    # win.wcToolBar.addAction(QIcon(QPixmap('icons/cltan2.gif')), 'Tangent 2 circles', a2d.clineTan2)
    win.wcToolBar.addAction(QIcon(QPixmap("icons/ccirc.gif")), "Circle", a2d.ccirc)
    # win.wcToolBar.addAction(QIcon(QPixmap('icons/cc3p.gif')), 'Circle by 3Pts', a2d.ccirc)
    # win.wcToolBar.addAction(QIcon(QPixmap('icons/cccirc.gif')), 'Concentric Circle', a2d.ccirc)
    # win.wcToolBar.addAction(QIcon(QPixmap('icons/cctan2.gif')), 'Circ Tangent x2', a2d.ccirc)
    # win.wcToolBar.addAction(QIcon(QPixmap('icons/cctan3.gif')), 'Circ Tangent x3', a2d.ccirc)
    win.wcToolBar.addSeparator()
    # win.wcToolBar.addAction(QIcon(QPixmap('icons/del_cel.gif')), 'Delete Constr', a2d.delCl)
    # Profile Line Toolbar buttons
    win.wgToolBar.addAction(QIcon(QPixmap("icons/line.gif")), "Line", a2d.line)
    win.wgToolBar.addAction(QIcon(QPixmap("icons/rect.gif")), "Rectangle", a2d.rect)
    # win.wgToolBar.addAction(QIcon(QPixmap('icons/poly.gif')), 'Polygon', a2d.geom)
    # win.wgToolBar.addAction(QIcon(QPixmap('icons/slot.gif')), 'Slot', a2d.geom)
    win.wgToolBar.addAction(QIcon(QPixmap("icons/circ.gif")), "Circle", a2d.circle)
    win.wgToolBar.addAction(
        QIcon(QPixmap("icons/arcc2p.gif")), "Arc Cntr-2Pts", a2d.arcc2p
    )
    win.wgToolBar.addAction(QIcon(QPixmap("icons/arc3p.gif")), "Arc by 3Pts", a2d.arc3p)
    win.wgToolBar.addSeparator()
    # win.wgToolBar.addAction(QIcon(QPixmap('icons/translate.gif')), 'Translate Profile', a2d.geom)
    # win.wgToolBar.addAction(QIcon(QPixmap('icons/rotate.gif')), 'Rotate Profile', a2d.geom)
    win.wgToolBar.addAction(
        QIcon(QPixmap("icons/del_el.gif")), "Delete Profile Elem", a2d.delEl
    )

    win.raise_()  # bring the app to the top
    app.exec_()
