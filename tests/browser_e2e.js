const { chromium } = require('playwright-core');
const fs = require('fs');
const assert = require('assert');

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox']});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const email = `browser-e2e-${Date.now()}@example.com`;
  const password = 'browser-test-password';
  const log = [];
  page.on('console', msg => log.push('console: ' + msg.text()));
  page.on('pageerror', err => log.push('pageerror: ' + err.message));

  await page.goto('http://127.0.0.1:18080/login.html');
  await page.click('#show-register');
  await page.fill('#register-email', email);
  await page.fill('#register-password', password);
  await page.click('#register-button');
  await page.waitForSelector('#register-success:not(:empty)');
  assert.match(await page.textContent('#register-success'), /Account created/);

  await page.click('#show-login');
  await page.fill('#login-email', email);
  await page.fill('#login-password', password);
  await page.click('#login-button');
  await page.waitForURL('**/index.html');
  assert.equal(await page.evaluate(() => sessionStorage.getItem('dietnerd_user')), email);

  await page.fill('#question', 'cached question');
  await page.click('#submit');
  await page.waitForFunction(() => document.querySelector('#chat-thread').textContent.includes('Cached browser-test answer'));
  const firstConversation = await page.evaluate(() => sessionStorage.getItem('dietnerd_conversation_id'));
  assert(firstConversation);
  assert.equal(await page.locator('.chat-message.user').count(), 1);
  assert.equal(await page.locator('.chat-message.assistant').count(), 1);

  await page.fill('#question', 'what about sleep?');
  await page.click('#submit');
  await page.waitForSelector('#similarQuestions button:last-child');
  await page.click('#similarQuestions button:last-child');
  await page.waitForFunction(() => document.querySelector('#chat-thread').textContent.includes('magnesium on sleep'));
  assert.equal(await page.locator('.chat-message.user').count(), 2);
  assert.equal(await page.locator('.chat-message.assistant').count(), 2);

  await page.click('#new-conversation');
  assert.equal(await page.evaluate(() => sessionStorage.getItem('dietnerd_conversation_id')), null);
  await page.fill('#question', 'fresh conversation question');
  await page.click('#submit');
  await page.waitForSelector('#similarQuestions button:last-child');
  await page.click('#similarQuestions button:last-child');
  await page.waitForFunction(() => document.querySelector('#chat-thread').textContent.includes('fresh conversation question'));
  const secondConversation = await page.evaluate(() => sessionStorage.getItem('dietnerd_conversation_id'));
  assert(secondConversation && secondConversation !== firstConversation);

  await page.selectOption('#conversation-select', firstConversation);
  await page.waitForFunction(() => document.querySelector('#chat-thread').textContent.includes('magnesium on sleep'));
  await page.selectOption('#conversation-select', secondConversation);
  await page.waitForFunction(() => document.querySelector('#chat-thread').textContent.includes('fresh conversation question'));

  fs.writeFileSync('tests/browser-upload.txt', 'Browser upload nutrition facts');
  await page.setInputFiles('#attachment-file', 'tests/browser-upload.txt');
  await page.waitForFunction(() => document.querySelector('#existing-attachments').textContent.includes('browser-upload.txt'));
  await page.fill('#question', 'summarize my upload');
  await page.click('#submit');
  await page.waitForFunction(() => document.querySelector('#chat-thread').textContent.includes('summarize my upload'));
  await page.click('[data-filename="browser-upload.txt"]');
  await page.waitForFunction(() => !document.querySelector('#existing-attachments').textContent.includes('browser-upload.txt'));

  await page.screenshot({path: '/downloads/chronicnerd-browser-answer.png', fullPage: true});

  await page.click('#delete-conversation');
  await page.waitForFunction(() => sessionStorage.getItem('dietnerd_conversation_id') === null);
  assert(!await page.locator(`#conversation-select option[value="${secondConversation}"]`).count());

  await page.screenshot({path: '/downloads/chronicnerd-browser-final.png', fullPage: true});
  fs.unlinkSync('tests/browser-upload.txt');
  assert.equal(log.filter(line => line.startsWith('pageerror:')).length, 0, log.join('\n'));
  console.log(JSON.stringify({status: 'passed', firstConversation, secondConversation, browser: await browser.version(), consoleEvents: log.length}));
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
