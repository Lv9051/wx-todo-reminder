"""验证真实控件适配的发送边界；不操作桌面、不发送消息。"""
import unittest
from unittest.mock import Mock,patch
from wechat_adapter import send_via_uia,private_via_uia,open_target,invoke_control


class NativeContactTests(unittest.TestCase):
    def setUp(self):
        self.driver=Mock()
        self.driver.current_chat.return_value='文件传输助手'
        self.driver._set_text.return_value=True
        self.body={'target_type':'contact','group_name':'文件传输助手','message':'待办提醒连接测试'}
        self.edit=self.driver._chat_input.return_value

    def test_native_full_text_then_single_enter(self):
        self.driver._read_edit_value.side_effect=['',self.body['message'],'']
        with patch('time.sleep'):
            private_via_uia(self.body,self.driver)
        self.edit.SendKeys.assert_called_once_with('{Enter}',waitTime=.1)

    def test_existing_draft_never_modified(self):
        self.driver._read_edit_value.return_value='用户自己的草稿'
        with self.assertRaisesRegex(RuntimeError,'草稿'):
            private_via_uia(self.body,self.driver)
        self.driver._set_text.assert_not_called()
        self.edit.SendKeys.assert_not_called()

    def test_inexact_body_never_sent_or_cleared(self):
        self.driver._read_edit_value.side_effect=['','测试','测试']
        with self.assertRaisesRegex(RuntimeError,'正文'):
            private_via_uia(self.body,self.driver)
        self.edit.SendKeys.assert_not_called()
        self.driver._set_text.assert_called_once_with(self.edit,self.body['message'])

    def test_check_never_types_or_sends(self):
        self.driver._read_edit_value.return_value=''
        private_via_uia(dict(self.body,operation='check'),self.driver)
        self.driver._set_text.assert_not_called()
        self.edit.SendKeys.assert_not_called()

    def test_after_send_failure_is_not_retryable(self):
        self.driver._read_edit_value.side_effect=['',self.body['message'],self.body['message']]
        phase={'attempted':False}
        with patch('time.sleep'),self.assertRaises(RuntimeError):
            send_via_uia(self.body,self.driver,phase)
        self.assertTrue(phase['attempted'])
        self.edit.SendKeys.assert_called_once_with('{Enter}',waitTime=.1)

    def test_search_selects_verified_result_not_stale_enter_selection(self):
        self.driver.current_chat.side_effect=['其他会话','文件传输助手']
        cell=Mock()
        self.driver._collect_results.return_value=[dict(name='文件传输助手',section='功能',aid='filehelper',cell=cell)]
        with patch('time.sleep'):
            open_target(self.body,self.driver)
        self.driver._click_ctrl.assert_called_once_with(cell)
        self.driver._search_box.return_value.SendKeys.assert_not_called()

    def test_ambiguous_search_never_opens_result(self):
        self.driver.current_chat.return_value='其他会话'
        self.driver._collect_results.return_value=[dict(name='文件传输助手',section='功能',aid=a,cell=Mock()) for a in ('one','two')]
        with patch('time.sleep'),self.assertRaisesRegex(RuntimeError,'唯一'):
            open_target(self.body,self.driver)
        self.driver._click_ctrl.assert_not_called()


class NativeGroupTests(unittest.TestCase):
    def setUp(self):
        self.driver=Mock()
        self.driver.current_chat.return_value='测试群'
        self.edit=self.driver._chat_input.return_value
        self.body=dict(target_type='group',group_name='测试群',member='小王',message='请交报告')
        self.cell=Mock();self.cell.Name='小王'
        self.listing=Mock();self.listing.GetChildren.return_value=[self.cell]
        self.driver._read_edit_value.side_effect=['','\ufffc','\ufffc 请交报告','']

    def test_real_mention_is_preserved_and_body_appended(self):
        with patch('time.sleep'),patch('wechat_adapter.mention_list',side_effect=[self.listing,self.listing,None]):
            send_via_uia(self.body,self.driver)
        self.driver._set_text.assert_not_called()
        self.driver._clip_set.assert_any_call(' 请交报告')
        self.driver._click_ctrl.assert_called_once_with(self.cell)
        calls=self.edit.SendKeys.call_args_list
        self.assertEqual(sum(c.args==('{Enter}',) for c in calls),1)
        self.assertTrue(any(c.args==('{End}{Ctrl}v',) for c in calls))

    def test_duplicate_member_does_not_send(self):
        self.listing.GetChildren.return_value=[self.cell,self.cell]
        self.driver._read_edit_value.side_effect=['','@小王']
        phase={'attempted':False}
        with patch('time.sleep'),patch('wechat_adapter.mention_list',return_value=self.listing),self.assertRaisesRegex(RuntimeError,'唯一'):
            send_via_uia(self.body,self.driver,phase)
        self.assertFalse(phase['attempted'])
        self.assertFalse(any(c.args==('{Enter}',) for c in self.edit.SendKeys.call_args_list))
        self.driver._set_text.assert_called_once_with(self.edit,'')

    def test_plain_text_at_is_not_accepted_as_mention(self):
        self.driver._read_edit_value.side_effect=['','@小王','@小王']
        with patch('time.sleep'),patch('wechat_adapter.mention_list',side_effect=[self.listing,self.listing,None]),self.assertRaisesRegex(RuntimeError,'标记'):
            send_via_uia(self.body,self.driver)
        self.assertFalse(any(c.args==('{Enter}',) for c in self.edit.SendKeys.call_args_list))


class DirectActionTests(unittest.TestCase):
    def test_legacy_action_calls_control_without_mouse(self):
        control=Mock();control.GetLegacyIAccessiblePattern.return_value.DefaultAction="双击"
        invoke_control(control)
        control.GetLegacyIAccessiblePattern.return_value.DoDefaultAction.assert_called_once()
        control.Click.assert_not_called()
        control.GetInvokePattern.assert_not_called()

    def test_unsupported_action_has_no_coordinate_fallback(self):
        control=Mock();control.GetLegacyIAccessiblePattern.return_value=None
        control.GetInvokePattern.return_value=None
        with self.assertRaisesRegex(RuntimeError,"未进行鼠标点击"):
            invoke_control(control)
        control.Click.assert_not_called()


if __name__=='__main__':
    unittest.main()
