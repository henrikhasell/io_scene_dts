// Declare datablocks and spawn shapes BEFORE the client joins.
// Torque transmits datablocks during the connection handshake; one created
// afterwards never reaches the client, which then renders a shape whose
// datablock it does not have -- and takes the engine down with it.
$DtsxOrigin = "-1 -50 118";

function StageShape(%name, %slot, %scale)
{
   if (%scale $= "") %scale = 1;
   %db = "StageDb" @ %slot;
   if (!isObject(%db))
      eval("datablock StaticShapeData(" @ %db @ ") { shapeFile = \"dtsx/" @ %name @ ".dts\"; };");
   %o = new StaticShape() {
      dataBlock = %db;
      position = (getWord($DtsxOrigin,0) + %slot * 4) SPC getWord($DtsxOrigin,1) SPC getWord($DtsxOrigin,2);
      scale = %scale SPC %scale SPC %scale;
   };
   MissionGroup.add(%o);
   $Staged[%slot] = %o;
   $StagedName[%slot] = %name;
   echo("STAGED " @ %name @ " slot " @ %slot);
}

function ViewSlot(%slot)
{
   %p = LocalClientConnection.player;
   %o = $Staged[%slot];
   if (!isObject(%o)) { echo("no object in slot " @ %slot); return; }
   %t = %o.getTransform();
   // stand back along -Y and look at it
   %p.setTransform((getWord(%t,0)) SPC (getWord(%t,1) - 6) SPC (getWord(%t,2) + 1) SPC "0 0 1 0");
   echo("VIEWING " @ $StagedName[%slot]);
}

function PlayOn(%slot, %seq)
{
   if (isObject($Staged[%slot])) { $Staged[%slot].playThread(0, %seq); echo("thread " @ %seq); }
}
echo("stage loaded");

function BringToMe(%slot, %dist)
{
   if (%dist $= "") %dist = 5;
   %p = LocalClientConnection.player;
   %o = $Staged[%slot];
   if (!isObject(%o)) { echo("no object in slot " @ %slot); return; }
   // getEyePoint() does not exist in this build; the eye transform's
   // translation is the same thing
   %eye = %p.getEyeTransform();
   %fwd = %p.getEyeVector();
   %x = getWord(%eye,0) + getWord(%fwd,0) * %dist;
   %y = getWord(%eye,1) + getWord(%fwd,1) * %dist;
   %z = getWord(%eye,2) + getWord(%fwd,2) * %dist;
   %o.setTransform(%x SPC %y SPC %z SPC "1 0 0 0");
   echo("BROUGHT " @ $StagedName[%slot] @ " to " @ %x SPC %y SPC %z);
}

function HideAllBut(%keep)
{
   for (%i = 0; %i < 20; %i++)
   {
      if (!isObject($Staged[%i])) continue;
      if (%i == %keep) continue;
      // park it far below rather than delete: deleting while rendering is
      // what took the engine down earlier
      %t = $Staged[%i].getTransform();
      $Staged[%i].setTransform(getWord(%t,0) SPC getWord(%t,1) SPC "-500 1 0 0 0");
   }
}
