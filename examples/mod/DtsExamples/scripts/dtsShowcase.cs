//------------------------------------------------------------------------------
// The DTS add-on showcase: every example shape, labelled.
//
// Run from the tail of DtsShowcase.mis, which matters twice over:
//
//   - CreateServer destroys all server-side state before it loads the mission,
//     so datablocks declared any earlier would be gone.
//   - Torque transmits datablocks to a client during the connection handshake.
//     Anything declared after a client joins never reaches it, and rendering a
//     shape whose datablock the client lacks is an access violation rather than
//     a warning.  The mission trailer is after the first and before the second.
//------------------------------------------------------------------------------

// shape file, then the label that floats above it
$DtsShowcase[0]  = "01_detail_levels\tDetail Levels + Collision";
$DtsShowcase[1]  = "02_billboards\tBillboards (flat + Z-axis)";
$DtsShowcase[2]  = "03_sorted_foliage\tSorted Mesh (BSP draw order)";
$DtsShowcase[3]  = "04_blend_modes\tBlend Modes";
$DtsShowcase[4]  = "05_material_flags\tMaterial Flags";
$DtsShowcase[5]  = "06_skin_animation\tSkinned Mesh + Sequence";
$DtsShowcase[6]  = "07_vertex_animation\tVertex Animation";
$DtsShowcase[7]  = "08_material_frames\tMaterial Frames";
$DtsShowcase[8]  = "09_sequence_triggers\tSequence + Triggers";
$DtsShowcase[9]  = "10_ground_frames\tGround Frames";
$DtsShowcase[10] = "11_visibility\tObject Visibility";
$DtsShowcase[11] = "12_node_scale\tNode Scale Animation";
$DtsShowcase[12] = "13_decals\tDecals";
$DtsShowcase[13] = "14_ifl_material\tIFL Material";
$DtsShowcase[14] = "15_dsq_animation\tDSQ Animation";
$DtsShowcaseCount = 15;

// One row, centred on x = 0, so the showcase reads as a legend.  A grid was
// worse: perspective compressed the back rows' labels into the middle of the
// screen and they overprinted each other.  Along a row every label is at the
// same depth, and the three viewing stations below each frame five of them.
$DtsShowcaseY = -40;
$DtsShowcaseSpacing = 9;
$DtsShowcaseScale = 1.6;
$DtsShowcasePerStation = 5;
$DtsShowcaseStations = 3;

// How far each shape's lowest point sits below its origin, in model units, so
// the shape can be set down *on* the terrain rather than at it.  These are
// modelled around the origin in Blender, so half of them would otherwise be
// buried and the rest would hover; there is no console call that reads a
// shape's bounding box, so the numbers are read off the .dts and baked here by
// `build_examples.py -- --export <shapes dir> --lifts`, which rewrites every
// line below.  Order follows the sorted .dts filenames, which is the order the
// $DtsShowcase table above is in.
$DtsShowcaseLift[0]  = 0.37;    // 01_detail_levels
$DtsShowcaseLift[1]  = -0.25;   // 02_billboards
$DtsShowcaseLift[2]  = 0.62;    // 03_sorted_foliage
$DtsShowcaseLift[3]  = 0.25;    // 04_blend_modes
$DtsShowcaseLift[4]  = 0.15;    // 05_material_flags
$DtsShowcaseLift[5]  = 0.00;    // 06_skin_animation
$DtsShowcaseLift[6]  = -0.19;   // 07_vertex_animation
$DtsShowcaseLift[7]  = -0.25;   // 08_material_frames
$DtsShowcaseLift[8]  = 0.25;    // 09_sequence_triggers
$DtsShowcaseLift[9]  = -0.35;   // 10_ground_frames
$DtsShowcaseLift[10] = 0.15;    // 11_visibility
$DtsShowcaseLift[11] = -0.35;   // 12_node_scale
$DtsShowcaseLift[12] = 0.45;    // 13_decals
$DtsShowcaseLift[13] = -0.25;   // 14_ifl_material
$DtsShowcaseLift[14] = -0.40;   // 15_dsq_animation

/// The x a shape stands at.
function DtsShowcaseX(%index)
{
   return -1 * ((($DtsShowcaseCount - 1) * $DtsShowcaseSpacing) / 2)
          + %index * $DtsShowcaseSpacing;
}

function DtsShowcaseSpawn(%index)
{
   %entry = $DtsShowcase[%index];
   %shape = getField(%entry, 0);
   %label = getField(%entry, 1);

   %db = "DtsShowcaseData" @ %index;
   if (!isObject(%db))
      eval("datablock StaticShapeData(" @ %db @ ") { shapeFile = \"dtsx/" @ %shape @ ".dts\"; };");
   if (!isObject(%db))
   {
      error("DtsShowcase: could not make a datablock for " @ %shape);
      return;
   }

   %x = DtsShowcaseX(%index);
   %y = $DtsShowcaseY;
   %ground = getTerrainHeight(%x SPC %y);
   %z = %ground + $DtsShowcaseLift[%index] * $DtsShowcaseScale;

   %obj = new StaticShape() {
      dataBlock = %db;
      position = %x SPC %y SPC %z;
      rotation = "1 0 0 0";
      scale = $DtsShowcaseScale SPC $DtsShowcaseScale SPC $DtsShowcaseScale;
   };
   MissionGroup.add(%obj);
   $DtsShowcaseObj[%index] = %obj;

   echo("DtsShowcase: " @ %shape @ " -> " @ %label);
}

/// Label one shape with a waypoint.
///
/// The waypoint is what names the shape: it draws on the HUD and on the
/// commander map, so the row reads as a legend rather than as a line of grey
/// boxes.  Team 0 means everyone sees it.  Alternating heights keep
/// neighbouring labels from overprinting -- the text is drawn at a fixed size,
/// so at nine units apart a label is wider than the shape it names.
function DtsShowcaseMark(%index)
{
   if (isObject($DtsShowcaseMarker[%index]))
      return;

   %x = DtsShowcaseX(%index);
   %y = $DtsShowcaseY;
   %marker = new WayPoint() {
      position = %x SPC %y SPC (getTerrainHeight(%x SPC %y) + 4 + (%index % 2) * 3);
      rotation = "1 0 0 0";
      scale = "1 1 1";
      dataBlock = "WayPointMarker";
      name = getField($DtsShowcase[%index], 1);
      team = "0";
      lockCount = "0";
      homingCount = "0";
   };
   MissionGroup.add(%marker);
   $DtsShowcaseMarker[%index] = %marker;
}

/// Label only the shapes at %station, or all of them when %station is not one.
///
/// A waypoint that is off-screen still draws, clamped to the edge of the view,
/// so the ten shapes a station is not looking at pile their names up in the
/// corner and bury the five it is.  There is no way to hide one -- WayPoint has
/// no setHidden -- so the labels are deleted and re-made, which is what the
/// stock training missions do (Training3.cs:549).
function DtsShowcaseLabels(%station)
{
   %first = %station * $DtsShowcasePerStation;
   %last = %first + $DtsShowcasePerStation - 1;
   %all = !(%station >= 0 && %station < $DtsShowcaseStations);

   for (%i = 0; %i < $DtsShowcaseCount; %i++)
   {
      if (%all || (%i >= %first && %i <= %last))
         DtsShowcaseMark(%i);
      else if (isObject($DtsShowcaseMarker[%i]))
      {
         $DtsShowcaseMarker[%i].delete();
         $DtsShowcaseMarker[%i] = "";
      }
   }
}

function DtsShowcaseBuild()
{
   for (%i = 0; %i < $DtsShowcaseCount; %i++)
      DtsShowcaseSpawn(%i);
   DtsShowcaseLabels(-1);
   echo("DtsShowcase: " @ $DtsShowcaseCount @ " shapes placed");
}

/// Stand the player where the labels can be read.
///
/// The control object has to be the Player and not a Camera: waypoint names
/// are drawn by the client HUD only while a Player is controlled, so a Camera
/// sees the shapes with no legend at all.
///
/// %station 0..2 frames five shapes each; anything else backs off far enough
/// to see the whole row at once, where the labels do overprint -- fifteen of
/// them will not fit across 800 pixels at any distance.
function DtsShowcaseView(%station)
{
   %player = LocalClientConnection.player;
   if (!isObject(%player))
   {
      error("DtsShowcase: no player to move");
      return;
   }

   DtsShowcaseLabels(%station);

   if (%station >= 0 && %station < $DtsShowcaseStations)
   {
      %x = DtsShowcaseX(%station * $DtsShowcasePerStation
                        + mFloor($DtsShowcasePerStation / 2));
      %back = 24;
      %eye = 3;
   }
   else
   {
      %x = 0;
      %back = 70;
      %eye = 8;
   }

   %y = $DtsShowcaseY + %back;
   // Torque's identity rotation faces +y and the row is to the south, so every
   // station is turned through pi about z.
   %player.setTransform(%x SPC %y SPC (getTerrainHeight(%x SPC %y) + %eye)
                        SPC "0 0 1 3.14159");
}

/// Start every shape's sequence, so the animated examples are animating.
/// Called after the build; safe to call again from the console.
function DtsShowcasePlay()
{
   DtsShowcaseThread(5,  "Bend");
   DtsShowcaseThread(6,  "Wave");
   DtsShowcaseThread(7,  "Flip");
   DtsShowcaseThread(8,  "Spin");
   DtsShowcaseThread(9,  "Run");
   DtsShowcaseThread(10, "Pulse");
   DtsShowcaseThread(11, "Throb");
   DtsShowcaseThread(13, "Play");
   DtsShowcaseThread(14, "Swing");
   // 13_decals holds its Damage sequence part-way instead of looping, so the
   // burns that appear late in the ramp are switched on
   DtsShowcaseHold(12, "Damage", 0.95);
   echo("DtsShowcase: sequences playing");
}

function DtsShowcaseThread(%index, %seq)
{
   %o = $DtsShowcaseObj[%index];
   if (!isObject(%o))
      return;
   %o.playThread(0, %seq);
   %o.setThreadDir(0, true);
}

function DtsShowcaseHold(%index, %seq, %pos)
{
   %o = $DtsShowcaseObj[%index];
   if (!isObject(%o))
      return;
   %o.playThread(0, %seq);
   %o.setThreadPos(0, %pos);
   %o.stopThread(0);
}

DtsShowcaseBuild();
DtsShowcasePlay();
echo("dtsShowcase.cs loaded");
