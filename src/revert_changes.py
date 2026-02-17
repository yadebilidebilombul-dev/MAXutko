import os
import shutil

# Define the source and destination directories
src_dir = './src'
dst_dir = '.'

# Iterate through all files in the source directory
for filename in os.listdir(src_dir):
    # Construct the full path to the file
    filepath = os.path.join(src_dir, filename)

    # Check if the file is a regular file (not a directory)
    if os.path.isfile(filepath):
        # Move the file to the destination directory
        shutil.move(filepath, dst_dir)

        # Print a message to confirm the file was moved
        print(f"Moved {filename} to {dst_dir}")