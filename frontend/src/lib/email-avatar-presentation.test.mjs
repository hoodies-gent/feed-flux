import assert from 'node:assert/strict';
import test from 'node:test';

import { getAvatarPresentation } from './email-avatar-presentation.mjs';

test('email avatars use stable sender initials and palette classes', () => {
  const first = getAvatarPresentation('Sarah Chen');
  const repeated = getAvatarPresentation('Sarah Chen');

  assert.equal(first.initials, 'SC');
  assert.deepEqual(repeated, first);
  assert.equal(getAvatarPresentation('GitHub').initials, 'G');
  assert.equal(getAvatarPresentation(' ').initials, '?');
});
