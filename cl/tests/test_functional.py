# coding=utf-8
"""
Functional testing of courtlistener
"""
from django.test import LiveServerTestCase
from selenium import webdriver
import sys

class BasicUserTest(LiveServerTestCase):

    fixtures = ['test_court.json', 'authtest_data.json',
        'judge_judy.json', 'test_objects_search.json']

    @classmethod
    def setUpClass(cls):
        cls.screenshot = True
        for arg in sys.argv:
            if 'liveserver' in arg:
                cls.server_url = 'http://' + arg.split('=')[1]
                return
        super(BasicUserTest, cls).setUpClass()
        cls.server_url = cls.live_server_url

    @classmethod
    def tearDownClass(cls):
        if cls.server_url == cls.live_server_url:
            super(BasicUserTest, cls).tearDownClass()

    def setUp(self):
        self.browser = webdriver.PhantomJS(
            executable_path='/usr/local/phantomjs/phantomjs',
            service_log_path='/var/log/courtlistener/django.log',
        )
        self.browser.implicitly_wait(1)
        self.browser.set_window_size(1024, 768)

    def tearDown(self):
        if self.screenshot:
            print '\nSaving screenshot...'
            self.browser.save_screenshot(type(self).__name__ + '.png')
        self.browser.quit()


    def test_basic_homepage_search_and_signin(self):
        # Alice Anonymous navigates to the CL website.
        self.browser.get(self.server_url)

        # At a glance, Alice can see the Latest Opinions, Latest Oral Arguments,
        # the searchbox (obviously important), and a place to sign in
        page_text = self.browser.find_element_by_tag_name('body').text
        self.assertIn('Latest Opinions', page_text)
        self.assertIn('Latest Oral Arguments', page_text)

        search_box = self.browser.find_element_by_id('id_q')
        search_button = self.browser.find_element_by_id('search-button')
        self.assertIn('Search', search_button.text)

        self.assertIn('Sign in / Register', page_text)

        # Alice remembers this Lissner guy and wonders if he's been involved
        # in any litigation. She types his name into the search box and hits
        # Enter
        search_box.send_keys('lissner\n')

        # The browser brings her to a search engine result page with some
        # results. She notices her query is still in the searchbox and
        # has the ability to refine via facets
        search_box = self.browser.find_element_by_id('id_q')
        self.assertEqual('lissner', search_box.get_attribute('value'))

        facet_sidebar = self.browser.\
            find_element_by_id('sidebar-facet-placeholder')
        self.assertIn('Precedential Status', facet_sidebar.text)

        # Wanting to keep an eye on this Lissner guy, she decides to sign-in
        # and so she can create an alert
        sign_in = self.browser.find_element_by_link_text('Sign in / Register')
        sign_in.click()

        # she providers her usename and password to sign in
        page_text = self.browser.find_element_by_tag_name('body').text
        self.assertIn('Sign In', page_text)
        self.assertIn('Username', page_text)
        self.assertIn('Password', page_text)
        btn = self.browser.find_element_by_css_selector('button[type="submit"]')
        self.assertEqual('Sign In', btn.text)

        self.browser.find_element_by_id('username').send_keys('pandora')
        self.browser.find_element_by_id('password').send_keys('password')
        btn.click()

        # upon redirect, she's brought back to her original search results
        # for 'lissner'
        page_text = self.browser.find_element_by_tag_name('body').text
        self.assertNotIn(
            'Please enter a correct username and password.',
            page_text
        )
        search_box = self.browser.find_element_by_id('id_q')
        self.assertEqual('lissner', search_box.get_attribute('value'))

        # She now sees the form for creating an alert
        self.assertIn('Create an Alert', page_text)
        self.assertIn('Give the alert a name', page_text)
        self.assertIn('How often should we notify you?', page_text)
        self.browser.find_element_by_id('id_name')
        self.browser.find_element_by_id('id_rate')
        btn = self.browser.find_element_by_id('alertSave')
        self.assertEqual('Create Alert', btn.text)

        # But she decides to wait until another time. Instead she decides she
        # will log out. She notices a Profile link dropdown in the top of the
        # page, clicks it, and selects Sign out
        profile_dropdown = self.browser.\
            find_element_by_css_selector('a.dropdown-toggle')
        self.assertEqual(profile_dropdown.text.strip(), u'Profile')

        dropdown_menu = self.browser.\
            find_element_by_css_selector('ul.dropdown-menu')
        self.assertIsNone(dropdown_menu.get_attribute('display'))

        profile_dropdown.click()

        sign_out = self.browser.find_element_by_link_text('Sign out')
        sign_out.click()

        # She receives a sign out confirmation with links back to the homepage,
        # the block, and an option to sign back in.
        page_text = self.browser.find_element_by_tag_name('body').text
        self.assertIn('You Have Successfully Signed Out', page_text)
        links = self.browser.find_elements_by_tag_name('a')
        self.assertIn('Go to the homepage', [link.text for link in links])
        self.assertIn('Read our blog', [link.text for link in links])

        bootstrap_btns = self.browser.find_elements_by_css_selector('a.btn')
        self.assertIn('Sign Back In', [btn.text for btn in bootstrap_btns])
