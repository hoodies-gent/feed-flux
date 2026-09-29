export function getFeedLoadMode(hasLoadedFeed) {
  return hasLoadedFeed ? 'background' : 'initial';
}

export function shouldRenderFeedError({ feedCount, error }) {
  return feedCount === 0 && Boolean(error);
}
