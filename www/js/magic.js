let updateFrequency = parseInt( window.location.hash.substr(1) ) || 15;
let updateCounter = updateFrequency-1;

async function putRouteDataInElementWithId( route, elementId )
{
try
{
let data = await $.getJSON(`/api/v3/routes/${route.pointA}-${route.pointB}/sailings/upcoming`, { limit: 7 } );

let dataContainer = $( `#${elementId}` );

dataContainer.empty();

for(let i=0;i<data.sailings[0].length;i++)
{
let sailing = data.sailings[0][i];

dataContainer.append( `<li>${sailing.messages.relativeDay}: ${sailing.messages.friendlyTime} (${sailing.messages.relativeTime})<br /><span style="color: red;">${sailing.warning}</span></li>` );
}

$( `#next` ).attr( `data-${elementId}`, `${data.sailings[0][0].messages.friendlyTime} - ${data.sailings[0][0].warning}` );
}
catch( error )
{
console.error( error );
}

return true;
}

async function main() {
await putRouteDataInElementWithId( { pointA: 'bow', pointB: 'hsb' }, 'bow' );
await putRouteDataInElementWithId( { pointA: 'hsb', pointB: 'bow' }, 'hsb' );

$('#next').text( `Next sailing from Bowen: ${$('#next').attr( 'data-bow' )}, and Horseshoe Bay: ${$('#next').attr( 'data-hsb' )}` );

updateCounter++;
if( updateCounter > updateFrequency )
{
updateCounter = 1;
}

return true;
}

main();

setInterval( main, 60000 );
