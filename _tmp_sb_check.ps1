$k='sb_publishable_hnSosJoSD9SNMnNrT_G9XQ_VAqtcmBy'
$h=@{apikey=$k;Authorization="Bearer $k"}
$base='https://tjpjcmmcaortvubtnykm.supabase.co/rest/v1'
Write-Output '== family_data 总行数 =='
try{ (Invoke-RestMethod -Uri "$base/family_data?select=family_id,key" -Headers $h -Method GET) | Group-Object key | Select-Object -First 25 | ForEach-Object { '{0}  x{1}' -f $_.Name, $_.Count } }catch{ Write-Output ('ERR '.$_.Exception.Message) }
Write-Output '== 表清单探测 =='
foreach($t in @('families','family_backup','diet_knowledge','knowledge','user_families')){
  try{ $c=(Invoke-RestMethod -Uri "$base/$t`?select=*&limit=1" -Headers $h).Count; Write-Output "$t OK rows>=$c" }catch{ Write-Output "$t -" }
}
