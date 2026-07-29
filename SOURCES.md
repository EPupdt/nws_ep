# Source and publication policy

Europe Pulse is a source-attributed news monitoring and editorial-summary service. It is not an article-republication service.

## Default rule for editorial publishers

Unless a publisher has granted a suitable licence in writing, public outputs contain only:

- article title;
- publisher name;
- publication time;
- direct canonical link.

Publisher-provided summaries, images, video, full text and paid content are not published or retained in the public repository. The current editorial sources use the `title_link_only` mode in `config/sources.yml`.

## Europe Pulse editorial summaries

`Europe Now` and `Top stories` are new, concise summaries based on source-attributed reporting. They must use original neutral wording, must not quote or closely reproduce source text, and must link to one or two original articles. A summary is omitted when the available source data does not support it.

## Source classes

| Class | Public treatment | Examples |
|---|---|---|
| Green | Use only in the scope of an explicit publisher/official licence | official EU institution feeds, licensed partners |
| Amber | title, publisher, date and direct link only | editorial news outlets without an explicit syndication licence |
| Red | do not collect or publish until written permission is obtained | feeds whose terms prohibit professional or collective use |

Each source is reviewed for technical availability, terms of use, attribution requirements and whether its role is live news or analysis. A source can be disabled at any time.

## Current exception: Euractiv

Euractiv is a valuable Brussels-policy source, but its direct RSS and sitemap endpoints currently return HTTP 403 to cloud collectors. It remains configured as disabled rather than being scraped around that protection. It can be restored when Euractiv provides a compatible feed, API or written permission.

## Corrections and removal

Europe Pulse will remove or change a source integration on a substantiated rights-holder request. Contact details and the public editorial policy will be added before the production EuropePulse.eu launch.
