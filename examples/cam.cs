// A stationary viewpoint.  The spawned player falls, so anything positioned
// relative to it has moved by the time a screenshot is taken; a Camera on the
// Observer datablock stays where it is put.  Observer is stock, so the client
// already has it -- a datablock created after the join never reaches the
// client and takes the engine down when something using it renders.
function ViewShape(%slot, %dist, %height)
{
   if (%dist $= "") %dist = 5;
   if (%height $= "") %height = 0;
   %o = $Staged[%slot];
   if (!isObject(%o)) { echo("VIEW no object in slot " @ %slot); return; }

   if (!isObject($Cam))
   {
      $Cam = new Camera() { dataBlock = Observer; };
      MissionCleanup.add($Cam);
   }
   // Park every other staged shape well away first: they were all spawned
   // before the join (they have to be), so they are all in the world at once
   // and the last one viewed is still sitting on the stage.
   for (%i = 0; %i < 32; %i++)
   {
      if (!isObject($Staged[%i]) || %i == %slot) continue;
      $Staged[%i].setTransform("0 0 -400 1 0 0 0");
   }
   %x = 0; %y = 0; %z = 300;
   %o.setTransform(%x SPC %y SPC %z SPC "1 0 0 0");
   $Cam.setTransform(%x SPC (%y - %dist) SPC (%z + %height) SPC "1 0 0 0");
   LocalClientConnection.setControlObject($Cam);
   echo("VIEWING " @ $StagedName[%slot] @ " from " @ %dist);
}

function ViewAngle(%dist, %height, %ax, %ay, %az, %angle)
{
   $Cam.setTransform("0" SPC (0 - %dist) SPC (300 + %height) SPC %ax SPC %ay SPC %az SPC %angle);
   echo("camera moved");
}
echo("cam loaded");
