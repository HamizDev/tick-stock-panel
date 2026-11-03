"""Isolated regression test: ETF manual sync never writes the stock daily table."""
import importlib.util,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
BASE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('isolated_etf_kline_patch',BASE/'app/api/kline.py')
kline=importlib.util.module_from_spec(spec)
spec.loader.exec_module(kline)
class Repo:
    def __init__(self,types):self.types=types
    def resolve_asset_type(self,s):return self.types[s]
def request(types):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=Repo(types),capabilities=object())))
def test_etf_single_goes_to_etf_tables():
    with patch.object(kline.kline_sync,'sync_and_persist_daily_batch') as stock,patch('app.services.index_sync.sync_and_persist_etf_daily',return_value=60) as etf:
        result=kline.sync_symbol(request({'159560.SZ':'etf'}),symbol='159560.SZ',days=120)
        assert result=={'symbol':'159560.SZ','rows_written':60}
        stock.assert_not_called()
        assert etf.call_args.kwargs['symbols_override']==['159560.SZ']
        assert etf.call_args.kwargs['count']==120

def test_etf_batch_mixed_routes_each_to_correct_store():
    with patch.object(kline.kline_sync,'sync_and_persist_daily_batch',return_value=40) as stock,patch('app.services.index_sync.sync_and_persist_etf_daily',return_value=80) as etf:
        result=kline.sync_batch(request({'159500.SZ':'etf','600519.SH':'stock','159560.SZ':'etf'}),['159500.SZ','600519.SH','159560.SZ'],days=60)
        assert result['rows_written']==120
        assert stock.call_args.args[0]==['600519.SH']
        assert etf.call_args.kwargs['symbols_override']==['159500.SZ','159560.SZ']

def test_empty_etf_result_is_reported():
    with patch.object(kline.kline_sync,'sync_and_persist_daily_batch') as stock,patch('app.services.index_sync.sync_and_persist_etf_daily',return_value=0):
        result=kline.sync_symbol(request({'589220.SH':'etf'}),symbol='589220.SH',days=60)
        assert result['rows_written']==0 and result['zero_row_symbols']==['589220.SH']
        stock.assert_not_called()

def test_stock_legacy_contract_unchanged():
    with patch.object(kline.kline_sync,'sync_and_persist_daily_batch',return_value=12) as stock,patch('app.services.index_sync.sync_and_persist_etf_daily') as etf:
        result=kline.sync_symbol(request({'600519.SH':'stock'}),symbol='600519.SH',days=60)
        assert result['rows_written']==12 and 'zero_row_symbols' not in result
        assert stock.call_args.args[0]==['600519.SH']
        etf.assert_not_called()
