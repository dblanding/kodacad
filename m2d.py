#!/usr/bin/env python
#
# Copyright 2020 Doug Blanding (dblanding@gmail.com)
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


from OCC.Core.BRep import BRep_Tool
from OCC.Core.TopoDS import TopoDS_Vertex, topods_Vertex


class M2D:
    """Methods for creating and drawing elements on 2D workplanes"""

    def __init__(self, win, display):
        self.win = win
        self.display = display

    #############################################
    #
    # Create 2d Construction Line functions
    #
    #############################################

    def add_vertex_to_xyPtStack(self, shapeList):
        """Helper function to convert vertex to gp_Pnt and put on ptStack."""
        wp = self.win.activeWp
        for shape in shapeList:
            if isinstance(shape, TopoDS_Vertex):  # Guard against wrong type
                vrtx = topods_Vertex(shape)
                pnt = BRep_Tool.Pnt(vrtx)  # convert vertex to type <gp_Pnt>
                trsf = wp.Trsf.Inverted()  # New transform. Don't invert wp.Trsf
                pnt.Transform(trsf)
                pt2d = (pnt.X(), pnt.Y())  # 2d point
                self.win.xyPtStack.append(pt2d)
            else:
                print(f"(Unwanted) shape type: {type(shape)}")

    def processLineEdit(self):
        """pop value from lineEditStack and place on floatStack or ptStack."""

        text = self.win.lineEditStack.pop()
        if "," in text:
            try:
                xstr, ystr = text.split(",")
                p = (float(xstr) * self.win.unitscale,
                     float(ystr) * self.win.unitscale)
                self.win.xyPtStack.append(p)
            except:
                print("Problem with processing line edit stack")
        else:
            try:
                self.win.floatStack.append(float(text))
            except ValueError as e:
                print(f"{e}")

    def clineH(self):
        """Horizontal construction line"""
        if self.win.xyPtStack:
            wp = self.win.activeWp
            p = self.win.xyPtStack.pop()
            self.win.xyPtStack = []
            wp.hcl(p)
            self.win.draw_wp(self.win.activeWpUID)
        else:
            self.win.registerCallback(self.clineHC)
            self.display.SetSelectionModeVertex()
            self.win.xyPtStack = []
            self.win.clearLEStack()
            self.win.lineEdit.setFocus()
            statusText = "Select point or enter Y-value for horizontal cline."
            self.win.statusBar().showMessage(statusText)

    def clineHC(self, shapeList, *args):
        """Callback (collector) for clineH"""
        self.add_vertex_to_xyPtStack(shapeList)
        if self.win.lineEditStack:
            self.processLineEdit()
        if self.win.floatStack:
            y = self.win.floatStack.pop() * self.win.unitscale
            pnt = (0, y)
            self.win.xyPtStack.append(pnt)
        if self.win.xyPtStack:
            self.clineH()

    def clineV(self):
        """Vertical construction line"""
        if self.win.xyPtStack:
            wp = self.win.activeWp
            p = self.win.xyPtStack.pop()
            self.win.xyPtStack = []
            wp.vcl(p)
            self.win.draw_wp(self.win.activeWpUID)
        else:
            self.win.registerCallback(self.clineVC)
            self.display.SetSelectionModeVertex()
            self.win.xyPtStack = []
            self.win.clearLEStack()
            self.win.lineEdit.setFocus()
            statusText = "Select point or enter X-value for vertcal cline."
            self.win.statusBar().showMessage(statusText)

    def clineVC(self, shapeList, *args):
        """Callback (collector) for clineV"""
        self.add_vertex_to_xyPtStack(shapeList)
        if self.win.lineEditStack:
            self.processLineEdit()
        if self.win.floatStack:
            x = self.win.floatStack.pop() * self.win.unitscale
            pnt = (x, 0)
            self.win.xyPtStack.append(pnt)
        if self.win.xyPtStack:
            self.clineV()

    def clineHV(self):
        """Horizontal + Vertical construction lines"""
        if self.win.xyPtStack:
            wp = self.win.activeWp
            p = self.win.xyPtStack.pop()
            self.win.xyPtStack = []
            wp.hvcl(p)
            self.win.draw_wp(self.win.activeWpUID)
        else:
            self.win.registerCallback(self.clineHVC)
            self.display.SetSelectionModeVertex()
            self.win.xyPtStack = []
            self.win.clearLEStack()
            self.win.lineEdit.setFocus()
            statusText = "Select point or enter x,y coords for H+V cline."
            self.win.statusBar().showMessage(statusText)

    def clineHVC(self, shapeList, *args):
        """Callback (collector) for clineHV"""
        self.add_vertex_to_xyPtStack(shapeList)
        if self.win.lineEditStack:
            self.processLineEdit()
        if self.win.xyPtStack:
            self.clineHV()

    def cline2Pts(self):
        """Construction line through two points"""
        if len(self.win.xyPtStack) == 2:
            wp = self.win.activeWp
            p2 = self.win.xyPtStack.pop()
            p1 = self.win.xyPtStack.pop()
            wp.acl(p1, p2)
            self.win.xyPtStack = []
            self.win.draw_wp(self.win.activeWpUID)
        else:
            self.win.registerCallback(self.cline2PtsC)
            self.display.SetSelectionModeVertex()
            self.win.xyPtStack = []
            self.win.clearLEStack()
            self.win.lineEdit.setFocus()
            statusText = "Select 2 points for Construction Line."
            self.win.statusBar().showMessage(statusText)

    def cline2PtsC(self, shapeList, *args):
        """Callback (collector) for cline2Pts"""
        self.add_vertex_to_xyPtStack(shapeList)
        if self.win.lineEditStack:
            self.processLineEdit()
        if len(self.win.xyPtStack) == 2:
            self.cline2Pts()

    def clineAng(self):
        """Construction line through a point and at an angle"""
        if self.win.xyPtStack and self.win.floatStack:
            wp = self.win.activeWp
            text = self.win.floatStack.pop()
            angle = float(text)
            pnt = self.win.xyPtStack.pop()
            wp.acl(pnt, ang=angle)
            self.win.xyPtStack = []
            self.win.draw_wp(self.win.activeWpUID)
        else:
            self.win.registerCallback(self.clineAngC)
            self.display.SetSelectionModeVertex()
            self.win.xyPtStack = []
            self.win.floatStack = []
            self.win.lineEditStack = []
            self.win.lineEdit.setFocus()
            statusText = "Select point on WP (or enter x,y coords) then enter angle."
            self.win.statusBar().showMessage(statusText)

    def clineAngC(self, shapeList, *args):
        """Callback (collector) for clineAng"""
        self.add_vertex_to_xyPtStack(shapeList)
        self.win.lineEdit.setFocus()
        if self.win.lineEditStack:
            self.processLineEdit()
        if self.win.xyPtStack and self.win.floatStack:
            self.clineAng()

    def clineRefAng(self):
        pass

    def clineAngBisec(self):
        pass

    def clineLinBisec(self):
        """Linear bisector between two points"""
        if len(self.win.xyPtStack) == 2:
            wp = self.win.activeWp
            pnt2 = self.win.xyPtStack.pop()
            pnt1 = self.win.xyPtStack.pop()
            wp.lbcl(pnt1, pnt2)
            self.win.xyPtStack = []
            self.win.draw_wp(self.win.activeWpUID)
        else:
            self.win.registerCallback(self.clineLinBisecC)
            self.display.SetSelectionModeVertex()

    def clineLinBisecC(self, shapeList, *args):
        """Callback (collector) for clineLinBisec"""
        self.add_vertex_to_xyPtStack(shapeList)
        if len(self.win.xyPtStack) == 2:
            self.clineLinBisec()

    def clinePara(self):
        pass

    def clinePerp(self):
        pass

    def clineTan1(self):
        pass

    def clineTan2(self):
        pass

    def ccirc(self):
        """Create a c-circle from center & radius or center & Pnt on circle"""
        wp = self.win.activeWp
        if len(self.win.xyPtStack) == 2:
            p2 = self.win.xyPtStack.pop()
            p1 = self.win.xyPtStack.pop()
            rad = wp.p2p_dist(p1, p2)
            wp.circle(p1, rad, constr=True)
            self.win.xyPtStack = []
            self.win.floatStack = []
            self.win.draw_wp(self.win.activeWpUID)
        elif self.win.xyPtStack and self.win.floatStack:
            pnt = self.win.xyPtStack.pop()
            rad = self.win.floatStack.pop() * self.win.unitscale
            wp.circle(pnt, rad, constr=True)
            self.win.xyPtStack = []
            self.win.floatStack = []
            self.win.draw_wp(self.win.activeWpUID)
        else:
            self.win.registerCallback(self.ccircC)
            self.display.SetSelectionModeVertex()
            self.win.xyPtStack = []
            self.win.floatStack = []
            self.win.lineEditStack = []
            self.win.lineEdit.setFocus()
            statusText = "Pick center of construction circle and enter radius."
            self.win.statusBar().showMessage(statusText)

    def ccircC(self, shapeList, *args):
        """callback (collector) for ccirc"""
        self.add_vertex_to_xyPtStack(shapeList)
        self.win.lineEdit.setFocus()
        if self.win.lineEditStack:
            self.processLineEdit()
        if len(self.win.xyPtStack) == 2:
            self.ccirc()
        if self.win.xyPtStack and self.win.floatStack:
            self.ccirc()

    #############################################
    #
    # Create 2d Edge Profile functions
    #
    #############################################

    def line(self):
        """Create a profile geometry line between two end points."""
        if len(self.win.xyPtStack) == 2:
            wp = self.win.activeWp
            pnt2 = self.win.xyPtStack.pop()
            pnt1 = self.win.xyPtStack.pop()
            wp.line(pnt1, pnt2)
            self.win.xyPtStack = []
            self.win.draw_wp(self.win.activeWpUID)
        else:
            self.win.registerCallback(self.lineC)
            self.display.SetSelectionModeVertex()
            self.win.xyPtStack = []
            self.win.lineEdit.setFocus()
            statusText = "Select 2 end points for line."
            self.win.statusBar().showMessage(statusText)

    def lineC(self, shapeList, *args):
        """callback (collector) for line"""
        self.add_vertex_to_xyPtStack(shapeList)
        self.win.lineEdit.setFocus()
        if self.win.lineEditStack:
            self.processLineEdit()
        if len(self.win.xyPtStack) == 2:
            self.line()

    def rect(self):
        """Create a profile geometry rectangle from two diagonally opposite corners."""
        if len(self.win.xyPtStack) == 2:
            wp = self.win.activeWp
            pnt2 = self.win.xyPtStack.pop()
            pnt1 = self.win.xyPtStack.pop()
            wp.rect(pnt1, pnt2)
            self.win.xyPtStack = []
            self.win.draw_wp(self.win.activeWpUID)
        else:
            self.win.registerCallback(self.rectC)
            self.display.SetSelectionModeVertex()
            self.win.xyPtStack = []
            self.win.lineEdit.setFocus()
            statusText = "Select 2 points for Rectangle."
            self.win.statusBar().showMessage(statusText)

    def rectC(self, shapeList, *args):
        """callback (collector) for rect"""
        self.add_vertex_to_xyPtStack(shapeList)
        self.win.lineEdit.setFocus()
        if self.win.lineEditStack:
            self.processLineEdit()
        if len(self.win.xyPtStack) == 2:
            self.rect()

    def circle(self):
        """Create a geometry circle from cntr & rad or cntr & pnt on circle."""
        wp = self.win.activeWp
        if len(self.win.xyPtStack) == 2:
            p2 = self.win.xyPtStack.pop()
            p1 = self.win.xyPtStack.pop()
            rad = wp.p2p_dist(p1, p2)
            wp.circle(p1, rad, constr=False)
            self.win.xyPtStack = []
            self.win.floatStack = []
            self.win.draw_wp(self.win.activeWpUID)
        elif self.win.xyPtStack and self.win.floatStack:
            pnt = self.win.xyPtStack.pop()
            rad = self.win.floatStack.pop() * self.win.unitscale
            wp.circle(pnt, rad, constr=False)
            self.win.xyPtStack = []
            self.win.floatStack = []
            self.win.draw_wp(self.win.activeWpUID)
        else:
            self.win.registerCallback(self.circleC)
            self.display.SetSelectionModeVertex()
            self.win.xyPtStack = []
            self.win.floatStack = []
            self.win.lineEditStack = []
            self.win.lineEdit.setFocus()
            statusText = "Pick center and enter radius or pick center & 2nd point."
            self.win.statusBar().showMessage(statusText)

    def circleC(self, shapeList, *args):
        """callback (collector) for circle"""
        self.add_vertex_to_xyPtStack(shapeList)
        self.win.lineEdit.setFocus()
        if self.win.lineEditStack:
            self.processLineEdit()
        if len(self.win.xyPtStack) == 2:
            self.circle()
        if self.win.xyPtStack and self.win.floatStack:
            self.circle()

    def arcc2p(self):
        """Create an arc from center pt, start pt and end pt."""
        wp = self.win.activeWp
        if len(self.win.xyPtStack) == 3:
            pe = self.win.xyPtStack.pop()
            ps = self.win.xyPtStack.pop()
            pc = self.win.xyPtStack.pop()
            wp.arcc2p(pc, ps, pe)
            self.win.xyPtStack = []
            self.win.floatStack = []
            self.win.draw_wp(self.win.activeWpUID)
        else:
            self.win.registerCallback(self.arcc2pC)
            self.display.SetSelectionModeVertex()
            self.win.xyPtStack = []
            statusText = "Pick center of arc, then start then end point."
            self.win.statusBar().showMessage(statusText)

    def arcc2pC(self, shapeList, *args):
        """callback (collector) for arcc2p"""
        self.add_vertex_to_xyPtStack(shapeList)
        self.win.lineEdit.setFocus()
        if self.win.lineEditStack:
            self.processLineEdit()
        if len(self.win.xyPtStack) == 3:
            self.arcc2p()

    def arc3p(self):
        """Create an arc from start pt, end pt, and 3rd pt on the arc."""
        wp = self.win.activeWp
        if len(self.win.xyPtStack) == 3:
            ps = self.win.xyPtStack.pop()
            pe = self.win.xyPtStack.pop()
            p3 = self.win.xyPtStack.pop()
            wp.arc3p(ps, pe, p3)
            self.win.xyPtStack = []
            self.win.floatStack = []
            self.win.draw_wp(self.win.activeWpUID)
        else:
            self.win.registerCallback(self.arc3pC)
            self.display.SetSelectionModeVertex()
            self.win.xyPtStack = []
            statusText = "Pick start point on arc, then end then 3rd point on arc."
            self.win.statusBar().showMessage(statusText)

    def arc3pC(self, shapeList, *args):
        """Callback (collector) for arc3p"""
        self.add_vertex_to_xyPtStack(shapeList)
        self.win.lineEdit.setFocus()
        if self.win.lineEditStack:
            self.processLineEdit()
        if len(self.win.xyPtStack) == 3:
            self.arc3p()

    def geom(self):
        pass

    #############################################
    #
    # 2D Delete functions
    #
    #############################################

    def delCl(self):
        """Delete selected 2d construction element.

        Todo: Get this working. Able to pre-select lines from the display
        as type <AIS_InteractiveObject> but haven't figured out how to get
        the type <AIS_Line> (or the cline or Geom_Line that was used to make
        it)."""
        self.win.registerCallback(self.delClC)
        statusText = "Select a construction element to delete."
        self.win.statusBar().showMessage(statusText)
        self.display = self.win.canvas._self.display.Context
        print(self.display.NbSelected())  # Use shift-select for multiple lines
        selected_line = self.display.SelectedInteractive()
        if selected_line:
            print(type(selected_line))  # <AIS_InteractiveObject>
            print(selected_line.GetOwner())  # <Standard_Transient>

    def delClC(self, shapeList, *args):
        """Callback (collector) for delCl"""
        print(shapeList)
        print(args)
        self.delCl()

    def delEl(self):
        """Delete selected geometry profile element."""
        wp = self.win.activeWp
        if self.win.shapeStack:
            while self.win.shapeStack:
                shape = self.win.shapeStack.pop()
                if shape in wp.edgeList:
                    wp.edgeList.remove(shape)
            self.win.redraw()
        else:
            self.win.registerCallback(self.delElC)
            self.display.SetSelectionModeEdge()
            self.win.xyPtStack = []
            statusText = "Select a geometry profile element to delete."
            self.win.statusBar().showMessage(statusText)

    def delElC(self, shapeList, *args):
        """Callback (collector) for delEl"""
        for shape in shapeList:
            self.win.shapeStack.append(shape)
        if self.win.shapeStack:
            self.delEl()
