# svg-to-spooker

A tool built in python to take an SVG that fits the following condition and converts it into a spooker table

* A single enclosed shape with no voids or crossing edges

The tool will probably break and complain if it doesn't satisfy those requirements, if you have any issues, DM me on discord (@alphawuff)

## Output format

A list of x,y pairs of coordinates scaled to fit in the 3x3 field of the spooker table generator available at https://spooker-table-generator.tiiny.site/:

e.g.
```
0.1234,0.5678
0.2345,0.6789
...
```
I tried to match the vertex count to the way the table generator likes, but I was guessing a little bit at how they implemented it, idk if it'll be perfect.

Any vertex order issues can be fixed within the table generator itself by just clicking "Auto-Fix Point Order" near the top of the generator, next to the validation button.
## What is "resolution"?

Resolution (default 50) just specifies how many vertices you want the output table to have. 
Lower values may truncate curves, higher values may cause tables to take longer for the generator (or devs) to parse.
Try and find the lowest value that conserves the general shape of your table


## GUI

Pretty self explanatory. Load an SVG at the top left, enter a desired vertex resolution, then hit convert.
